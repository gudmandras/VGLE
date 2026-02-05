import random, tempfile, time, os, shutil, gc
from datetime import datetime

from qgis.PyQt.QtCore import QCoreApplication, QVariant, QEventLoop, QTimer
from processing.core.Processing import Processing
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
                       QgsProcessingParameterField,
                       QgsProcessingParameterString,
                       QgsProcessingParameterDefinition,
                       QgsProcessingParameterFolderDestination)
from qgis import processing
import qgis.utils
from . import vgle_layers



class TopDownAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
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

        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        self.permanent_data['inputLayer'] = inputLayer
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()
        tempLayer = vgle_layers.createTempLayer(self.permanent_data['inputLayer'], parameters["OutputDirectory"],
                                                'topdown', timeStamp)
        self.permanent_data['tempLayer'] = tempLayer                                             
        layer, self.holderAttribute = vgle_layers.setHolderField(self.permanent_data['tempLayer'], parameters["AssignedByField"])
        self.permanent_data['layer'] = layer
        context.temporaryLayerStore().addMapLayer(layer)
        firstResult = processing.run("Polygon Grouper:polygon_grouper", {
                'Inputlayer': self.permanent_data['layer'],
                'Preference': False,
                'AssignedByField': [self.holderAttribute],
                'BalancedByField': parameters['BalancedByField'],
                'Tolerance': parameters['Tolerance'],
                'DistanceThreshold': parameters['DistanceThreshold'],
                'SwapToGet': parameters['SwapToGet'],
                'OutputDirectory': parameters['OutputDirectory'],
                'OnlySelected': False, 
                'Single': parameters['Single'],
                'StrictHDI': parameters['StrictHDI'],
                'StrictHFI': parameters['StrictHFI'],
                'Simply': parameters['Simply'],
                'Stats': True
            }, context=context, feedback=feedback)
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
        for key, group in groups.items():
            self.selectGroup(group, self.permanent_data['layer'], self.holderAttribute, context)
            feedback.pushInfo(f'Group {key} processing started with {self.permanent_data["groupLayer"].featureCount()} features')
            tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                    'Inputlayer': self.permanent_data['groupLayer'],
                    'Preference': True,
                    'AssignedByField': [self.holderAttribute],
                    'BalancedByField': parameters['BalancedByField'],
                    'Tolerance': parameters['Tolerance'],
                    'DistanceThreshold': parameters['DistanceThreshold'],
                    'SwapToGet': parameters['SwapToGet'],
                    'OutputDirectory': parameters['OutputDirectory'],
                    'OnlySelected': False, 
                    'Single': parameters['Single'],
                    'StrictHDI': parameters['StrictHDI'],
                    'StrictHFI': parameters['StrictHFI'],
                    'Simply': parameters['Simply'],
                    'Stats': False
                }, context=context, feedback=feedback)
            try:
                groupedLayer = tempResult['OUTPUT']
                groupedMerged = tempResult['MERGED']
                groupedLayer.setName(f"Group {key} - {self.permanent_data['swappedLayer'].name()} - {parameters['Postfix']}")
                groupedLayer.triggerRepaint()
                #rename_file(groupedLayer, f"Group {key} - {swappedLayer.name()}")
                groupedMerged.setName(f"Group {key} - { self.permanent_data['mergedLayer'].name()} - {parameters['Postfix']}")
                groupedMerged.triggerRepaint()
                #rename_file(groupedMerged, f"Group {key} - {mergedLayer.name()}")
                self.permanent_data['layer'].removeSelection()
                results['OUTPUT'].append(groupedLayer)
            except KeyError:
                groupedLayer = self.permanent_data["groupLayer"]
                groupedLayer.setName(f"Group {key} - {self.permanent_data['swappedLayer'].name()} - {parameters['Postfix']} no changes")
                #rename_file(groupedLayer, f"Group {key} - {swappedLayer.name()} - no changes")
                groupedLayer.triggerRepaint()
                self.permanent_data['layer'].removeSelection()
                QgsProject.instance().addMapLayer(groupedLayer, False)
                root = QgsProject().instance().layerTreeRoot()
                root.insertLayer(0, groupedLayer)
                results['OUTPUT'].append(groupedLayer)
            del self.permanent_data['groupLayer'], tempResult
            gc.collect()
            QgsApplication.processEvents()
        return results

    def selectGroup(self, group, layer, idAttribute, context):
        quoted_values = [QgsExpression.quotedValue(v) for v in group]
        expression = f'"{idAttribute}" IN ({",".join(map(str, quoted_values))})'
        request = QgsFeatureRequest().setFilterExpression(expression)

        selectedFeatures = layer.materialize(request)
        selectedFeatures.setName(f"temp_group_{random.randint(1000,9999)}")
        self.permanent_data['groupLayer'] = selectedFeatures
        

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
        return False

    r_folder = str(r_folder)

    if not r_folder or not os.path.exists(r_folder):
        return False
    return r_folder

def copyR_script(r_script_path):
    settings = QgsSettings()
    dest_folder = settings.value("Processing/Configuration/R_SCRIPTS_FOLDER")

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
