from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (QgsProject,
                       QgsApplication,
                       QgsProcessing,
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
import random, tempfile, time
from datetime import datetime
from . import vgle_layers



class BottomUpAlgorithm(QgsProcessingAlgorithm):

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
        return BottomUpAlgorithm()

    def name(self):
        return 'upstream'

    def displayName(self):
        return self.tr('Bottom up script')

    def group(self):
        return self.tr('vgle')

    def groupId(self):
        return ''

    def shortHelpString(self):

        return self.tr("Bottom up workflow script for polygon grouper plugin.\n Preference is given to the selected feature's.\n")

    def processAlgorithm(self, parameters, context, feedback):
        #import ptvsd
        #ptvsd.debug_this_thread()
        results = {}
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        if not len(inputLayer.selectedFeatures()) or inputLayer.selectedFeatureCount() == 0:
            feedback.reportError('Please select features to process', fatalError=True)
            return {}   
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()

        tempLayer = vgle_layers.createTempLayer(inputLayer, parameters["OutputDirectory"],
                                                'bottomup', timeStamp)
        layer, self.holderAttribute = vgle_layers.setHolderField(tempLayer, parameters["AssignedByField"])

        selectedHolders = [h[self.holderAttribute] for h in inputLayer.selectedFeatures()]

        try:
            swappedLayer = processing.run("Polygon Grouper:polygon_grouper", {
                    'Inputlayer': layer,
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
        except KeyError as e:
            feedback.reportError('No change was made - bottom up process terminated!', fatalError=True)
            return {}
        feedback.setProgress(50)
        
        if parameters['holdersThreshold'] > len(selectedHolders):
            feedback.pushInfo('Group creation started')
            project = QgsProject.instance()
            change_log = [layer for layer in project.mapLayers().values() if layer.name() == 'Change log'][-1]
            holders = list(set([f['Holder ID'] for f in change_log.getFeatures()]))
            
            group = selectedHolders.copy() 
            currentHolders = selectedHolders.copy() 

            while parameters['holdersThreshold'] >= len(group) and currentHolders:
                feedback.pushInfo(f'Current number of holders: {len(group)}')
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
        else:
            results['OUTPUT'] = swappedLayer
        inputLayer.removeSelection()
        return results

                
    def selectRandomHolder(self, layer, holders):
        startingHolder = random.choice(holders)  
        holderRows = [feature for feature in layer.getFeatures() if feature['Holder ID'] == startingHolder]
        validHolder = False
        while not validHolder:
            for holderRow in holderRows:
                getHolder = holderRow['Get from land holder ID']
                giveHolder = holderRow['Transfer to land holder ID']

                if getHolder or giveHolder:
                    validHolder = True
                else:
                    continue
            
            if not validHolder:
                holders.remove(startingHolder)
                startingHolder = random.choice(holders) 
                holderRows = [feature for feature in layer.getFeatures() if feature['Holder ID'] == startingHolder]

        return startingHolder, holders

    def selectGroup(self, group, layer, idAttribute):
        for turn, part in enumerate(group):
            if turn == 0:
                expression = f'"{idAttribute}" = \'{part}\''
            else:
                expression += f'OR "{idAttribute}" = \'{part}\''
        layer.selectByExpression(expression)
        layer.triggerRepaint()
        QgsApplication.processEvents()
        algParams = {
            'INPUT': layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selectedFeatures = processing.run('native:saveselectedfeatures', algParams)["OUTPUT"]
        return selectedFeatures

            




        

        

        
