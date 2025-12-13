import random, tempfile, time, os, shutil
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
                       QgsProcessingMultiStepFeedback,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterField,
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
        self.addParameter(QgsProcessingParameterFolderDestination('OutputDirectory', 'Output directory',
                                                                  defaultValue=None, createByDefault=True))
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]
        self.counter = 0
        
        single = QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False)
        single.setFlags(single.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(single)
        strict = QgsProcessingParameterBoolean('Strict', "Strict conditions for neighbours method", defaultValue=False)
        strict.setFlags(strict.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict)
        simplfy = QgsProcessingParameterBoolean('Simply', "Simply algorithm to process big dataset",
                                                defaultValue=False)
        simplfy.setFlags(simplfy.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(simplfy)
        holdersTreshold = QgsProcessingParameterNumber('holdersThreshold', 'Minimal number of the holder in the group',
                                                       type=QgsProcessingParameterNumber.Integer,
                                                       minValue=0, defaultValue=20)
        holdersTreshold.setFlags(holdersTreshold.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(holdersTreshold)


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

        return self.tr("Top down  workflow script for polygon grouper plugin")

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

        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()
        tempLayer = vgle_layers.createTempLayer(inputLayer, parameters["OutputDirectory"],
                                                'topdown', timeStamp)
        layer, self.holderAttribute = vgle_layers.setHolderField(tempLayer, parameters["AssignedByField"])
        firstResult = processing.run("Polygon Grouper:polygon_grouper", {
                'Inputlayer': layer,
                'Preference': False,
                'AssignedByField': [self.holderAttribute],
                'BalancedByField': parameters['BalancedByField'],
                'Tolerance': parameters['Tolerance'],
                'DistanceThreshold': parameters['DistanceThreshold'],
                'SwapToGet': parameters['SwapToGet'],
                'OutputDirectory': parameters['OutputDirectory'],
                'OnlySelected': False, 
                'Single': parameters['Single'],
                'Strict': parameters['Strict'],
                'Simply': parameters['Simply'],
                'Stats': True
            }, context=context, feedback=feedback)
        swappedLayer = firstResult['OUTPUT']
        mergedLayer = firstResult['MERGED']
        feedback.setProgress(50)

        feedback.pushInfo('Group creation started')
        project = QgsProject.instance()
        frequency = [layerTemp for layerTemp in project.mapLayers().values() if layerTemp.name() == 'Swap frequency'][-1]

        result = processing.run("r:topdown", {
            'INPUT': frequency,
            'Group': os.path.join(parameters['OutputDirectory'], f'topdown_result_{timeStamp}.csv')
        })

        modularity = result['MODULARITY']
        feedback.pushInfo(f"Modularity Score: {modularity}")

        groupsCSV = result['Group']
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
        none_group_members = [f[self.holderAttribute] for f in layer.getFeatures() if f[self.holderAttribute] not in assigned_holders]
        if none_group_members:
            groups[none_group + 1] = none_group_members
        feedback.pushInfo('Group creation finished!')

        feedback.pushInfo('Group processing started!')
        results['OUTPUT'] = []
        for key, group in groups.items():
            groupLayer = self.selectGroup(group, layer, self.holderAttribute)
            feedback.pushInfo(f'Group {key} processing started with {groupLayer.featureCount()} features')
            feedback.pushInfo(str(type(groupLayer)))
            tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                    'Inputlayer': groupLayer,
                    'Preference': True,
                    'AssignedByField': [self.holderAttribute],
                    'BalancedByField': parameters['BalancedByField'],
                    'Tolerance': parameters['Tolerance'],
                    'DistanceThreshold': parameters['DistanceThreshold'],
                    'SwapToGet': parameters['SwapToGet'],
                    'OutputDirectory': parameters['OutputDirectory'],
                    'OnlySelected': False, 
                    'Single': parameters['Single'],
                    'Strict': parameters['Strict'],
                    'Simply': parameters['Simply'],
                    'Stats': False
                }, context=context, feedback=feedback)
            groupedLayer = tempResult['OUTPUT']
            groupedMerged = tempResult['MERGED']
            groupedLayer.setName(f"Group {key} - {swappedLayer.name()}")
            groupedLayer.triggerRepaint()
            groupedMerged.setName(f"Group {key} - {mergedLayer.name()}")
            groupedMerged.triggerRepaint()
            layer.removeSelection()
            results['OUTPUT'].append(groupedLayer)
            del groupLayer
            loop = QEventLoop()
            QTimer.singleShot(2000, loop.quit)
            loop.exec_()
        return results

    def selectGroup(self, group, layer, idAttribute):
        values = []
        fieldType = layer.fields().field(idAttribute).type()

        for value in group:
            if fieldType in (QVariant.Int, QVariant.LongLong):
                values.append(str(value))
            else:
                values.append(QgsExpression.quotedString(str(value)))
        expression = f'"{idAttribute}" IN ({",".join(values)})'

        layer.selectByExpression(expression)
        layer.triggerRepaint()
        QgsApplication.processEvents()

        algParams = {
            'INPUT': layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selectedFeatures = processing.run('native:saveselectedfeatures', algParams)["OUTPUT"]
        return selectedFeatures


def is_r_provider_installed():
    registry = QgsApplication.processingRegistry()
    providers = [p.id() for p in registry.providers()]
    if "r" in providers or "processing_r" in providers:
        return True
    else:
        return enable_r_plugin()

def enable_r_plugin():
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
        except Exception as e:
            return False
    else:
        try:
            settings_dir = QgsApplication.qgisSettingsDirPath()
            rsx_cache_path = os.path.join(settings_dir, "processing", "rscripts", os.path.basename(r_script_path))
            shutil.copy(r_script_path, rsx_cache_path)
        except Exception as e:
            return False   
    return True