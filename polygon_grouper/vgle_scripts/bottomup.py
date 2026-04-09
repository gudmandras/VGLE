from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (QgsProject,
                       QgsApplication,
                       QgsProcessing,
                       QgsProcessingAlgorithm,
                       QgsFeatureRequest,
                       QgsProcessingMultiStepFeedback,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterField,
                       QgsProcessingParameterDefinition,
                       QgsProcessingParameterFolderDestination)
from qgis import processing
import random, tempfile, time, os
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
        holdersTreshold = QgsProcessingParameterNumber('holdersThreshold', 'Maximal number of the holder in the group',
                                                       type=QgsProcessingParameterNumber.Integer,
                                                       minValue=0, defaultValue=20)
        holdersTreshold.setFlags(holdersTreshold.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(holdersTreshold)
        self.permanent_data = {}


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
        try:
            with open(os.path.join(os.path.dirname(__file__), 'shorthelp_bottomup.txt'), 'r',
                      encoding='utf-8') as file:
                return file.read()
        except FileNotFoundError:
            return "<html><body><p>Description file not found.</p></body></html>"
        except Exception as e:
            return f"<html><body><p>Error reading description file: {e}</p></body></html>"

    def processAlgorithm(self, parameters, context, feedback):
        results = {}
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        self.permanent_data['inputLayer'] = inputLayer

        if not len(self.permanent_data['inputLayer'].selectedFeatures()) or self.permanent_data['inputLayer'].selectedFeatureCount() == 0:
            feedback.reportError('Please select features to process', fatalError=True)
            return {}   
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()

        tempLayer = vgle_layers.createTempLayer(self.permanent_data['inputLayer'], parameters["OutputDirectory"],
                                                'bottomup', timeStamp)
        self.permanent_data['tempLayer'] = tempLayer
        layer, self.holderAttribute = vgle_layers.setHolderField(self.permanent_data['tempLayer'], parameters["AssignedByField"])
        self.permanent_data['layer'] = layer
        context.temporaryLayerStore().addMapLayer(layer)
        selectedHolders = [h[self.holderAttribute] for h in self.permanent_data['inputLayer'].selectedFeatures()]

        try:
            tempResult0 = processing.run("Polygon Grouper:polygon_grouper", {
                    'Inputlayer': self.permanent_data['layer'],
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
                    'Stats': True
                }, context=context, feedback=feedback)
            swappedLayer = tempResult0['OUTPUT']
            mergedLayer = tempResult0['MERGED']
            self.permanent_data['swappedLayer'] = swappedLayer
            self.permanent_data['mergedLayer'] = mergedLayer
        except KeyError as e:
            feedback.reportError('No change was made - bottom up process terminated!', fatalError=True)
            return {}
        feedback.setProgress(50)
        
        if parameters['holdersThreshold'] > len(selectedHolders):
            feedback.pushInfo('Group creation started')
            project = QgsProject.instance()
            change_log = [self.permanent_data['layer'] for self.permanent_data['layer'] in project.mapLayers().values() if self.permanent_data['layer'].name() == 'Change log'][-1]
            holders = list(set([f['Holder ID'] for f in change_log.getFeatures()]))
            
            group = selectedHolders.copy() 
            currentHolders = selectedHolders.copy() 

            while parameters['holdersThreshold'] >= len(group) and currentHolders:
                feedback.pushInfo(f'Current number of holders: {len(group)}')
                feedback.pushInfo(f'Current holders to process: {currentHolders}')
                nextHolders = []
                for currentHolder in currentHolders:
                    extendRows = []
                    holderRows = [feature for feature in change_log.getFeatures() if feature['Holder ID'] == currentHolder]
                    getRows = [feature['Get from land holder ID'] for feature in holderRows 
                    if feature['Get from land holder ID'] 
                    and feature['Get from land holder ID'] not in group
                    and feature['Get from land holder ID'] in holders]
                    giveRows = [feature['Transfer to land holder ID'] for feature in holderRows 
                    if feature['Transfer to land holder ID'] 
                    and feature['Transfer to land holder ID'] not in group
                    and feature['Transfer to land holder ID'] in holders]

                    extendRows = list(set(getRows + giveRows))

                    if not extendRows:
                        continue
                
                    for row in extendRows:
                        if row in holders:
                            holders.remove(row)

                    if parameters['holdersThreshold'] < (len(group) + len(extendRows)):
                        difference = parameters['holdersThreshold'] - len(group)
                        extendRows = extendRows[:difference]
                        group.extend(extendRows)
                        nextHolders = []
                        break
                    else:
                        group.extend(extendRows)
                        nextHolders.extend(extendRows)

                if not nextHolders:
                    break
                else:
                    currentHolders = nextHolders
            feedback.pushInfo(f'Final group len: {len(group)} - members:{group}')

            fields = self.permanent_data['swappedLayer'].fields()
            last_index = fields.count() - 1
            last_field_name = fields.at(last_index).name()

            self.selectGroup(group, self.permanent_data['swappedLayer'], last_field_name)
            feedback.pushInfo('Group created')
            tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                    'Inputlayer': self.permanent_data['groupLayer'],
                    'Preference': True,
                    'AssignedByField': [last_field_name],
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
            try:
                groupedLayer = tempResult['OUTPUT']
                groupedMerged = tempResult['MERGED']
                groupedLayer.setName(f"Bottomup group - {self.permanent_data['swappedLayer'].name()}")
                groupedMerged.setName(f"Bottomup group  - {self.permanent_data['mergedLayer'].name()}")
                groupedLayer.triggerRepaint()
                groupedMerged.triggerRepaint()
                results['OUTPUT'] = groupedLayer
            except KeyError as e:
                feedback.pushInfo(f'KeyError occurred: {e}')
                feedback.pushInfo('No changes were made for the created group')
                results['OUTPUT'] = self.permanent_data['swappedLayer']
                results['MERGED'] = self.permanent_data['mergedLayer']
        else:
            group = selectedHolders.copy() 
            fields = self.permanent_data['swappedLayer'].fields()
            last_index = fields.count() - 1
            last_field_name = fields.at(last_index).name()

            self.selectGroup(group, self.permanent_data['swappedLayer'], last_field_name)
            tempResult = processing.run("Polygon Grouper:polygon_grouper", {
                'Inputlayer': self.permanent_data['groupLayer'],
                'Preference': True,
                'AssignedByField': [last_field_name],
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
            try:
                groupedLayer = tempResult['OUTPUT']
                groupedMerged = tempResult['MERGED']
                groupedLayer.setName(f"Bottomup group - {self.permanent_data['swappedLayer'].name()}")
                groupedMerged.setName(f"Bottomup group  - {self.permanent_data['mergedLayer'].name()}")
                groupedLayer.triggerRepaint()
                groupedMerged.triggerRepaint()
                results['OUTPUT'] = groupedLayer
            except KeyError:
                feedback.pushInfo('No changes were made for the created group')
                results['OUTPUT'] = self.permanent_data['swappedLayer']
                results['MERGED'] = self.permanent_data['mergedLayer']
        self.permanent_data['inputLayer'].removeSelection()
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

        request = QgsFeatureRequest().setFilterExpression(expression)

        selectedFeatures = layer.materialize(request)
        selectedFeatures.setName(f"bottomup_group")
        self.permanent_data['groupLayer'] = selectedFeatures

            




        

        

        
