import random, tempfile, time, os, shutil, gc, sip
from datetime import datetime
from pathlib import Path
from osgeo import ogr

from qgis.PyQt.QtCore import QCoreApplication, QVariant, QEventLoop, QTimer
from processing.core.Processing import Processing
from processing.core.ProcessingConfig import ProcessingConfig
from qgis.core import (QgsProject,
                       QgsExpression,
                       QgsSettings,
                       QgsProcessing,
                       QgsApplication,
                       QgsVectorLayer,
                       QgsProcessingAlgorithm,
                       QgsFeatureRequest,
                       QgsProcessingUtils,
                       QgsProcessingMultiStepFeedback,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterEnum,
                       QgsVectorFileWriter,
                       QgsProcessingContext,
                       QgsProcessingParameterField,
                       QgsProcessingParameterString,
                       QgsDataSourceUri,
                       QgsProcessingParameterDefinition,
                       QgsProcessingParameterFolderDestination)
from qgis import processing
import qgis.utils
from . import vgle_layers, vgle_utils



class TopDownAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features',
                                                defaultValue=False))
        self.addParameter(QgsProcessingParameterField('AssignedByField', 'Holder by field',
                                                      type=QgsProcessingParameterField.Any,
                                                      parentLayerParameterName='Inputlayer', allowMultiple=True))
        self.addParameter(QgsProcessingParameterField('BalancedByField', 'Balanced by field',
                                                      type=QgsProcessingParameterField.Numeric,
                                                      parentLayerParameterName='Inputlayer',
                                                      allowMultiple=False, defaultValue=''))
        self.addParameter(QgsProcessingParameterNumber('Tolerance', 'Tolerance (%)',
                                                       type=QgsProcessingParameterNumber.Integer,
                                                       minValue=0, maxValue=100, defaultValue=5))
        self.addParameter(QgsProcessingParameterNumber('DistanceThreshold', 'Distance treshold (m)',
                                                       type=QgsProcessingParameterNumber.Integer,
                                                       minValue=0, defaultValue=1000))
        self.addParameter(QgsProcessingParameterEnum('SwapToGet', 'Swap to get',
                                                     options=['Neighbours', 'Closer', 'Neighbours, then closer',
                                                              'Closer, then neighbours'],
                                                     allowMultiple=False, defaultValue='Neighbours'))
        self.addParameter(QgsProcessingParameterString('Postfix', 'Postfix for the group files', defaultValue='first run'))
        self.addParameter(QgsProcessingParameterFolderDestination('OutputDirectory', 'Output directory',
                                                                  defaultValue=None, createByDefault=True))
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]

        onlySelected = QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features',
                                                     defaultValue=False)
        onlySelected.setFlags(onlySelected.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(onlySelected)
        single = QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False)
        single.setFlags(single.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(single)
        strict = QgsProcessingParameterBoolean('StrictHDI', "Strict condition on Holding Distance Indicator (HDI)", defaultValue=False)
        strict.setFlags(strict.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict)
        strict2 = QgsProcessingParameterBoolean('StrictHFI', "Strict condition on Holding Fragmentation Indicator (HFI)", defaultValue=False)
        strict2.setFlags(strict2.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict2)
        simplfy = QgsProcessingParameterBoolean('Simply', "Simply algorithm to process big dataset",
                                                defaultValue=False)
        simplfy.setFlags(simplfy.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(simplfy)
        self.permanent_data = {}
        self.backup_data = {}

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return TopDownAlgorithm()

    def name(self):
        return 'downstream'

    def displayName(self):
        return self.tr('Top down script')

    def group(self):
        return self.tr('vgle')

    def groupId(self):
        return ''

    def shortHelpString(self):
        try:
            with open(os.path.join(os.path.dirname(__file__), 'shorthelp_topdown.txt'), 'r',
                      encoding='utf-8') as file:
                return file.read()
        except FileNotFoundError:
            return "<html><body><p>Description file not found.</p></body></html>"
        except Exception as e:
            return f"<html><body><p>Error reading description file: {e}</p></body></html>"

    def processAlgorithm(self, parameters, context, feedback):
        #import ptvsd
        #ptvsd.debug_this_thread()
        results = {}

        if not is_r_provider_installed():
            feedback.reportError('R provider is not installed. Please install R provider to use this algorithm.',
                                 fatalError=True)
            return {}

        R_folder = checkR_folder() 
        if R_folder:
            copy_sucess = copyR_script(os.path.join(os.path.dirname(os.path.abspath(__file__)),'topdown.rsx'))
            if not copy_sucess:
                feedback.reportError("R Provider - RSX file cannot copied to the Rfolder.")
                return {}
        else:
            feedback.reportError("R Provider - R folder is not configured. Cannot install RSX script.")
            return {}

        if parameters['OnlySelected'] and parameters['Preference'] is not True:
            feedback.reportError(f"'Only use the selected features' parameters works only with "
                              f"'Give preference for the selected features parameter'. "
                              f"'Give preference for the selected features parameter' is enabled")
            parameters['Preference'] = True 
        
        if parameters['Preference']:
            algParams = {
                'INPUT': self.parameterAsVectorLayer(parameters, 'Inputlayer', context),
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            selectedPreference = processing.run("native:saveselectedfeatures", algParams, context=context, feedback=feedback, is_child_algorithm=True)
            self.permanent_data['selectedHoldings'] = context.takeResultLayer(selectedPreference['OUTPUT'])
            feedback.pushInfo(f"Selected features saved to temporary layer: {self.permanent_data['selectedHoldings']}")
            self.backup_data['selectedHoldings'] = vgle_utils.extractLayerData(self.permanent_data['selectedHoldings'])     
           
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        self.permanent_data['inputLayer'] = inputLayer
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()
        tempLayer = vgle_layers.createTempLayer(self.permanent_data['inputLayer'], parameters["OutputDirectory"],
                                                'topdown', timeStamp)
        self.permanent_data['tempLayer'] = tempLayer
        self.backup_data['tempLayer'] = vgle_utils.extractLayerData(self.permanent_data['tempLayer'])                                          
        layer, self.holderAttribute = vgle_layers.setHolderField(self.permanent_data['tempLayer'], parameters["AssignedByField"])
        context.temporaryLayerStore().addMapLayer(layer)
        self.permanent_data['layer'] = layer
        firstResult = processing.run("Polygon Grouper:polygon_grouper", {
                'Inputlayer': self.permanent_data['layer'],
                'Preference': parameters['Preference'],
                'AssignedByField': [self.holderAttribute],
                'BalancedByField': parameters['BalancedByField'],
                'Tolerance': parameters['Tolerance'],
                'DistanceThreshold': parameters['DistanceThreshold'],
                'SwapToGet': parameters['SwapToGet'],
                'OutputDirectory': parameters['OutputDirectory'],
                'OnlySelected': parameters['OnlySelected'],
                'Single': parameters['Single'],
                'StrictHDI': parameters['StrictHDI'],
                'StrictHFI': parameters['StrictHFI'],
                'Simply': parameters['Simply'],
                'Stats': True
            },  context=context, feedback=feedback)
        swappedLayer = firstResult['OUTPUT']
        mergedLayer = firstResult['MERGED']
        self.permanent_data['swappedLayer'] = swappedLayer
        self.permanent_data['mergedLayer'] = mergedLayer
        feedback.setProgress(50)

        feedback.pushInfo('Group creation started')
        project = QgsProject.instance()
        #frequency = [layerTemp for layerTemp in project.mapLayers().values() if layerTemp.name() == 'Swap frequency'][-1]
        frequency = None
        temp_layers = context.temporaryLayerStore().mapLayers().values()
        matching_layers = [lyr for lyr in temp_layers if lyr.name() == 'Swap frequency']
        if matching_layers:
            frequency = matching_layers[-1]
        else:
            # Fallback to project only if not found in temporary store
            project_layers = QgsProject.instance().mapLayers().values()
            matching_layers = [lyr for lyr in project_layers if lyr.name() == 'Swap frequency']
            if matching_layers:
                frequency = matching_layers[-1]

        if frequency.isEditable():
            frequency.commitChanges()

        csv_path = os.path.join(parameters['OutputDirectory'], f'topdown_result_{timeStamp}.csv')
        csv_sanitized = csv_path.replace('\\', '/')

        result = processing.run("r:topdown", {
            'INPUT': frequency,
            'Group': csv_sanitized
        }, context=context, feedback=feedback)

        groupsCSV = result['Group']
        feedback.pushInfo('Group CSV created at: ' + groupsCSV)
        uri = f"file:{groupsCSV}?type=csv&geomType=none"
        csvLayer = QgsVectorLayer(uri, "csv_no_geom", "delimitedtext")
        groups = {}
        assigned_holders = set()
        for f in csvLayer.getFeatures():
            holder_id = f[0] 
            group_id  = f[1] 
            groups.setdefault(group_id, []).append(holder_id)
            assigned_holders.add(holder_id)
        none_group = max(groups.keys())
        none_group_members = [f[self.holderAttribute] for f in self.permanent_data['layer'].getFeatures() if f[self.holderAttribute] not in assigned_holders]
        
        if none_group_members:
            groups[none_group + 1] = none_group_members
        feedback.pushInfo('Group creation finished!')

        feedback.pushInfo('Group processing started!')
        results['OUTPUT'] = []
        group_paths = {}
        for key, group in groups.items():
            self.selectGroup(group, self.permanent_data['layer'], self.holderAttribute, context, key)
            if parameters['Preference']:
                selectedFeatures = vgle_utils.checkVectorLayer(self.permanent_data['selectedHoldings'] , (self.backup_data['selectedHoldings'], 'selectedHoldings'))
                algParams = {
                    'INPUT': self.permanent_data['groupLayer'],
                    'PREDICATE': [3],
                    'METHOD': 0,
                    'INTERSECT': selectedFeatures,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                }
                processing.run("native:selectbylocation", algParams, is_child_algorithm=True)
            feedback.pushInfo(f'Group {key} processing started with {self.permanent_data["groupLayer"].featureCount()} features')
            try:
                tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                        'Inputlayer': self.permanent_data['groupLayer'],
                        'Preference': parameters['Preference'],
                        'AssignedByField': [self.holderAttribute],
                        'BalancedByField': parameters['BalancedByField'],
                        'Tolerance': parameters['Tolerance'],
                        'DistanceThreshold': parameters['DistanceThreshold'],
                        'SwapToGet': parameters['SwapToGet'],
                        'OutputDirectory': parameters['OutputDirectory'],
                        'OnlySelected':  parameters['OnlySelected'], 
                        'Single': parameters['Single'],
                        'StrictHDI': parameters['StrictHDI'],
                        'StrictHFI': parameters['StrictHFI'],
                        'Simply': False,
                        'Stats': False
                    }, context=context, feedback=feedback, is_child_algorithm=True)
            except Exception as e:
                feedback.pushError(f"Error processing group {key}: {e}")
                self.permanent_data['layer'] = vgle_utils.checkVectorLayer(self.permanent_data['layer'], self.backup_data['tempLayer'])
                self.selectGroup(group, self.permanent_data['layer'], self.holderAttribute, context, key)
                if parameters['Preference']:
                    selectedFeatures = vgle_utils.checkVectorLayer(self.permanent_data['selectedHoldings'] , (self.backup_data['selectedHoldings'], 'selectedHoldings'))
                    algParams = {
                        'INPUT': self.permanent_data['groupLayer'],
                        'PREDICATE': [3],
                        'METHOD': 0,
                        'INTERSECT': selectedFeatures,
                        'OUTPUT': 'TEMPORARY_OUTPUT'
                    }
                    processing.run("native:selectbylocation", algParams, is_child_algorithm=True)
                tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                        'Inputlayer': self.permanent_data['groupLayer'].id(),
                        'Preference': parameters['Preference'],
                        'AssignedByField': [self.holderAttribute],
                        'BalancedByField': parameters['BalancedByField'],
                        'Tolerance': parameters['Tolerance'],
                        'DistanceThreshold': parameters['DistanceThreshold'],
                        'SwapToGet': parameters['SwapToGet'],
                        'OutputDirectory': parameters['OutputDirectory'],
                        'OnlySelected':  parameters['OnlySelected'], 
                        'Single': parameters['Single'],
                        'StrictHDI': parameters['StrictHDI'],
                        'StrictHFI': parameters['StrictHFI'],
                        'Simply': False,
                        'Stats': False
                }, context=context, feedback=feedback, is_child_algorithm=True)
            try:
                group_paths[key] = (tempResult['OUTPUT'].source(), tempResult['MERGED'].source())
                remove_layer(tempResult['OUTPUT'].id())
                remove_layer(tempResult['MERGED'].id())
            except KeyError:
                groupedLayer = self.permanent_data["groupLayer"]
                groupedLayer.setName(f"topdown_group_{key}_{self.permanent_data['swappedLayer'].name()}_{parameters['Postfix']}_no_changes")
                
                save_path = os.path.join(parameters['OutputDirectory'], f"topdown_group_{key}_{self.permanent_data['swappedLayer'].name()}_{parameters['Postfix']}_no_changes.gpkg")
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                QgsVectorFileWriter.writeAsVectorFormatV2(
                    groupedLayer,
                    save_path,
                    context.transformContext(),
                    options
                )
                group_paths[key] = (save_path, save_path)

            try:
                storage = context.temporaryLayerStore()
                layer_ids = list(storage.mapLayers().keys())
                for l_id in layer_ids:
                    layer = storage.mapLayer(l_id)
                    if 'neighbours' in layer.name() or 'closer' in layer.name() or 'topdown_group' in layer.name() or 'no_changes' in layer.name():
                        storage.removeMapLayer(l_id)
            except Exception as e:
                pass
            self.permanent_data['layer'].removeSelection()
            try:
                del tempResult
                gc.collect()
            except:
                pass
            #QgsApplication.processEvents()
        outpath_1 = os.path.join(parameters['OutputDirectory'], f'topdown_groups_{timeStamp}.gpkg')
        outpath_2 = os.path.join(parameters['OutputDirectory'], f'topdown_groups_merged_{timeStamp}.gpkg')
        
        merge_to_geopackage([group[0] for group in group_paths.values()], outpath_1, context)
        merge_to_geopackage([group[1] for group in group_paths.values()], outpath_2, context)

        #layer1 = QgsVectorLayer(outpath_1, f"topdown_groups_{timeStamp}", "ogr")
        #layer2 = QgsVectorLayer(outpath_2, f"topdown_groups_merged_{timeStamp}", "ogr")

        self.locked_files = [group[0] for group in group_paths.values()] + [group[1] for group in group_paths.values()]

        results['OUTPUT'] = []
        results['MERGED'] = []
        self.add_groups(outpath_1, results, context, keyword='OUTPUT')
        self.add_groups(outpath_2, results, context, keyword='MERGED')

        return results

    def selectGroup(self, group, layer, idAttribute, context, key):
        layer = vgle_utils.checkVectorLayer(layer, self.backup_data['tempLayer'])
        quoted_values = [QgsExpression.quotedValue(v) for v in group]
        expression = f'"{idAttribute}" IN ({",".join(map(str, quoted_values))})'
        request = QgsFeatureRequest().setFilterExpression(expression)

        selectedFeatures = layer.materialize(request)
        selectedFeatures.setName(f"topdown_group_{key}")
        context.temporaryLayerStore().addMapLayer(selectedFeatures)
        self.permanent_data['groupLayer'] = selectedFeatures
        #QgsApplication.processEvents()

    def add_groups(self, geopackage, results, context, keyword=None):
        vlayer = QgsVectorLayer(geopackage, "test", "ogr")
        sublayers = vlayer.dataProvider().subLayers()
        layer_names = []

        for sublayer in sublayers:
            layer_name = sublayer.split("!!")[2]
            layer_names.append(layer_name)

        for layer_name in layer_names:
            uri = f"{geopackage}|layername={layer_name}"
            vlayer = QgsVectorLayer(uri, layer_name, "ogr")
            context.temporaryLayerStore().addMapLayer(vlayer)
            results[keyword].append(vlayer)
            context.addLayerToLoadOnCompletion(
            vlayer.id(), 
            QgsProcessingContext.LayerDetails(vlayer.name(), context.project(), keyword)
            )

    def postProcessAlgorithm(self, context, feedback):
        delete_shapefiles(self.locked_files)
        return {}


def is_r_provider_installed():
    registry = QgsApplication.processingRegistry()
    providers = [p.id() for p in registry.providers()]
    if "r" in providers or "processing_r" in providers:
        return True
    else:
        return enable_r_plugin()

def enable_r_plugin(reload=False):
    if reload:
        try:
            qgis.utils.unloadPlugin("processing_r")
            qgis.utils.loadPlugin("processing_r")
            qgis.utils.startPlugin("processing_r")
            return True
        except Exception as e:  
            return False

    try:
        if "processing_r" not in qgis.utils.plugins:
            qgis.utils.loadPlugin("processing_r")
            qgis.utils.startPlugin("processing_r")
            return True
    except Exception as e:
        return False

def checkR_folder():
    settings = QgsSettings()
    r_folder = settings.value("Processing/Configuration/R_FOLDER")

    if not r_folder:
        r_folder = ProcessingConfig.getSetting('R_FOLDER')
        if not r_folder:
            return False

    r_folder = str(r_folder)

    if not r_folder or not os.path.exists(r_folder):
        return False
    return r_folder

def copyR_script(r_script_path):
    settings = QgsSettings()
    dest_folder = settings.value("Processing/Configuration/R_SCRIPTS_FOLDER")
    if not dest_folder:
        dest_folder = ProcessingConfig.getSetting('R_SCRIPTS_FOLDER')

    if dest_folder:
        dest_path = os.path.join(dest_folder, os.path.basename(r_script_path))
        try:
            if not os.path.isfile(dest_path): 
                shutil.copy(r_script_path, dest_path)
            else:
                os.remove(dest_path)
                shutil.copy(r_script_path, dest_path)
            if not enable_r_plugin(reload=True):
                raise Exception("Failed to enable R plugin after copying RSX script.")
        except Exception as e:
            return False
    else:
        try:
            settings_dir = QgsApplication.qgisSettingsDirPath()
            rsx_cache_path = os.path.join(settings_dir, "processing", "rscripts", os.path.basename(r_script_path))
            if not os.path.isfile(rsx_cache_path): 
                shutil.copy(r_script_path, rsx_cache_path)
            else:
                os.remove(rsx_cache_path)
                shutil.copy(r_script_path, rsx_cache_path)
            if not enable_r_plugin(reload=True):
                raise Exception("Failed to enable R plugin after copying RSX script.")
        except Exception as e:
            return False   
    return True

def rename_file(layer, new_name):
    old_path = layer.source().split("|")[0]

    folder = os.path.dirname(old_path)
    old_base = os.path.splitext(os.path.basename(old_path))[0]
    extensions = [".shp", ".shx", ".dbf", ".prj", ".cpg"]

    # Remove layer from project
    QgsProject.instance().removeMapLayer(layer.id())

    layer.setDataSource("", "", "")
    del layer
    gc.collect()

    for ext in extensions:
        old_file = os.path.join(folder, old_base + ext)
        new_file = os.path.join(folder, new_name + ext)
        if os.path.exists(old_file):
            os.rename(old_file, new_file)

    # Reload layer
    iface.addVectorLayer(
        os.path.join(folder, new_base + ".shp"),
        new_base,
        "ogr"

    )

def remove_layer(layer_id):
    project = QgsProject.instance()
    project.removeMapLayer(layer_id)

def merge_to_geopackage(file_list, output_gpkg, context):
    """
    Takes a list of file paths and saves them into one GeoPackage.
    """   
    for i, file_path in enumerate(file_list):
        layer = QgsVectorLayer(file_path, str(Path(file_path).stem), "ogr")
        
        if not layer.isValid():
            print(f"Skipping invalid layer: {file_path}")
            continue

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer.name()
        
        if i == 0:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        else:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

        merge_result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            output_gpkg,
            context.transformContext(),
            options
        )
        if merge_result[0] == QgsVectorFileWriter.NoError:
            try:
                QgsProject.instance().removeMapLayer(layer.id())
                layer.setDataSource("", "", "")
                layer.dataProvider().reloadData()
                del layer
                gc.collect()
                delete_shapefiles([file_path])
            except Exception as e:
                print(f"Error cleaning up layer {file_path}: {e}")
        else:
            pass
        
def delete_shapefiles(shape_files):
    project = QgsProject.instance()
    
    for shape in shape_files:
        abs_shape = os.path.abspath(shape)
        
        layers_to_remove = [
            l.id() for l in project.mapLayers().values() 
            if os.path.abspath(l.source().split("|")[0]) == abs_shape
        ]
        if layers_to_remove:
            project.removeMapLayers(layers_to_remove)

        QgsApplication.processEvents()
        gc.collect()

        print(f"Deleting shapefile: {abs_shape}")
        
        success = QgsVectorFileWriter.deleteShapeFile(abs_shape)
        
        if not success:
            try:
                for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.qpj']:
                    part = abs_shape.replace('.shp', ext)
                    if os.path.exists(part):
                        os.remove(part)
                print(f"Manual deletion successful for {abs_shape}")
            except PermissionError:
                print(f"CRITICAL: {abs_shape} is still locked by an external process.")
