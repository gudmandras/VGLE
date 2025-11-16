import random, tempfile, time, os, shutil
from datetime import datetime

from qgis.PyQt.QtCore import QCoreApplication, QSettings
from processing.core.Processing import Processing
from qgis.core import (QgsProject,
                       QgsProcessing,
                       QgsApplication,
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
        swappedLayer = processing.run("Polygon Grouper:polygon_grouper", {
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
            }, context=context, feedback=feedback)['OUTPUT']
        feedback.setProgress(50)

        feedback.pushInfo('Group creation started')
        project = QgsProject.instance()
        frequency = [layer for layer in project.mapLayers().values() if layer.name() == 'Swap frequency'][-1]


        result = processing.run("rscripts:Topdown", {
            'INPUT': '/path/to/file.csv',
            'VALUE': 10,
            'OUTPUT': '/tmp/out.csv'
        })

        modularity = result['MODULARITY']
        feedback.pushInfo(f"Modularity Score: {modularity}")

        groups = result['Group']









        """
        feedback.pushInfo('Group creation started')
        project = QgsProject.instance()
        change_log = [layer for layer in project.mapLayers().values() if layer.name() == 'Change log'][-1]
        holders = list(set([f['Holder ID'] for f in change_log.getFeatures()]))

        #startingHolder, holders = self.selectRandomHolder(change_log, holders)
        startingHolder = inputLayer.selectedFeatures()[0][self.holderAttribute]
        
        #groups = {}
        group = [startingHolder]
        currentHolders = [startingHolder]
        while parameters['holdersThreshold'] >= len(group) and currentHolders:
            feedback.pushInfo(f'Current group size: {len(group)}')
            feedback.pushInfo(f'Current holders to process: {currentHolders}')
            nextHolders = []
            for currentHolder in currentHolders:
                holderRows = [feature for feature in change_log.getFeatures() if feature['Holder ID'] == currentHolder]
                getRows = [feature['Get from land holder ID'] for feature in holderRows 
                if feature['Get from land holder ID'] 
                and feature['Get from land holder ID'] not in group
                and feature['Get from land holder ID'] in holders]
                giveRows = [feature['Transfer to land holder ID'] for feature in holderRows 
                if feature['Transfer to land holder ID'] 
                and feature['Transfer to land holder ID'] not in group
                and feature['Transfer to land holder ID'] in holders]

                if startingHolder in holders:
                    holders.remove(startingHolder)
                for row in getRows + giveRows:
                    if row in holders:
                        holders.remove(row)

                extendRows = list(set(getRows + giveRows))

                if not extendRows:
                    break

                group.extend(extendRows)
                nextHolders.extend(extendRows)

                if parameters['holdersThreshold'] >= len(group):
                    break

            if not nextHolders:
                break
            else:
                currentHolders = nextHolders
        """    
            
            #groups[group[0]] = group
            #startingHolder, holders = self.selectRandomHolder(change_log, holders)
            #feedback.pushInfo(f'Group started from {group[0]}: {group}')
        """
        groupLayer = self.selectGroup(group, inputLayer, self.holderAttribute)
        feedback.pushInfo('Group created')
        groupedLayer = processing.run("Polygon Grouper:polygon_grouper", {
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
                'Stats': True
            }, context=context, feedback=feedback)['OUTPUT']

        results['OUTPUT'] = groupedLayer
        return results
        """

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
    settings = QSettings()
    r_folder = settings.value("processing/rscripts/RFolder")

    if not r_folder:
        return False

    r_folder = str(r_folder)

    if not r_folder or not os.path.exists(r_folder):
        return False
    return r_folder

def copyR_script(r_script_path):
    settings = QSettings()
    dest_folder = settings.value("processing/rscripts/RScriptsFolder")

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

    if not qgis.utils.isPluginLoaded("processing_r"):
        qgis.utils.loadPlugin("processing_r")
    Processing.initialize()
    return True
