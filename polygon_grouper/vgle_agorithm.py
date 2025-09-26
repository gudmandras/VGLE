__author__ = 'GOPA'
__date__ = '2024-09-05'
__copyright__ = '(C) 2024 by GOPA'
__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (Qgis,
                        QgsPoint,
                        QgsApplication,
                        QgsGeometry,
                        QgsProject,
                        QgsProcessing, 
                        QgsProcessingAlgorithm, 
                        QgsProcessingMultiStepFeedback, 
                        QgsProcessingParameterBoolean,
                        QgsProcessingParameterVectorLayer,
                        QgsProcessingParameterNumber,
                        QgsProcessingParameterFile,
                        QgsProcessingParameterEnum,
                        QgsProcessingParameterField,
                        QgsProcessingFeatureSourceDefinition,
                        QgsProcessingParameterDefinition,
                        QgsProcessingParameterFolderDestination,
                        QgsLayerTree, 
                        QgsFeature,
                        QgsField, 
                        QgsVectorFileWriter, 
                        QgsVectorLayer,
                        QgsFeatureRequest, 
                        QgsExpression,
                        QgsCoordinateReferenceSystem,
                        QgsWkbTypes)
import processing
import qgis.core
import os.path
from datetime import datetime
import time, copy, uuid, logging, itertools, sys, random, math, statistics, tempfile

class PolygonGrouper(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features', defaultValue=False))
        self.addParameter(QgsProcessingParameterField('AssignedByField', 'Holder by field', type=QgsProcessingParameterField.Any, parentLayerParameterName='Inputlayer', allowMultiple=True))
        self.addParameter(QgsProcessingParameterField('BalancedByField', 'Balanced by field', type=QgsProcessingParameterField.Numeric, parentLayerParameterName='Inputlayer', allowMultiple=False, defaultValue=''))
        self.addParameter(QgsProcessingParameterNumber('Tolerance', 'Tolerance (%)', type=QgsProcessingParameterNumber.Integer, minValue=0, maxValue=100, defaultValue=5))
        self.addParameter(QgsProcessingParameterNumber('DistanceTreshold', 'Distance treshold (m)', type=QgsProcessingParameterNumber.Integer, minValue=0, defaultValue=1000))
        self.addParameter(QgsProcessingParameterEnum('SwapToGet', 'Swap to get', options=['Neighbours','Closer','Neighbours, then closer','Closer, then neighbours', 'Hybrid'], allowMultiple=False, defaultValue='Neighbours'))
        self.addParameter(QgsProcessingParameterFolderDestination('OutputDirectory', 'Output directory', defaultValue=None, createByDefault=True))
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours", 'Hybrid']
        self.counter = 0
        
        onlySelected = QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features', defaultValue=False)
        onlySelected.setFlags(onlySelected.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(onlySelected)
        single = QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False)
        single.setFlags(single.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(single)
        strict = QgsProcessingParameterBoolean('Strict', "Strict conditions for neighbours method", defaultValue=False)
        strict.setFlags(strict.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict)
        simplfy = QgsProcessingParameterBoolean('Simplfy', "Simplfy algorithm to process big dataset", defaultValue=False)
        simplfy.setFlags(simplfy.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(simplfy)
        stats = QgsProcessingParameterBoolean('Stats', "Generate statistics", defaultValue=False)
        stats.setFlags(stats.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(stats)

    def name(self):
        return 'polygon_grouper'

    def displayName(self):
        return 'Polygon regrouper'

    def group(self):
        return 'vgle'

    def groupId(self):
        return ''

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        return """<html><body><h2>Algoritm description</h2>
        <p>This algorithm is employed for the purpose of regrouping polygons. The regrouping process entails the exchange of holder values in the 'holder field' between two or more polygons, on the condition that the sum of the values in the 'balanced by field' for the two holders is identical or the difference prior to and following the swap is less than a specified threshold. The objective of the process is to maximise the adjacency of the holders' polygons, or alternatively to minimise the separation between them, or both. The development of this algorithm was primarily to support the reduction of land fragmentation by land exchange, yet it is sufficiently general to be applied to a number of other areas, including the regrouping of agent territories or analogous geometric tasks. </p>
        <h2>Input parameters</h2>
        <h3>Input layer</h3>
        <p>Vector layer with polygon geometries.</p>
        <h3>Give preference for the selected polygons</h3>
        <p>The selected features in the input layer will be used as seed polygons, and the grouping will be around these features. Without these, the seed polygons are the largest polygons per the assigned by field unique values.</p>
        <h3>Holder by field</h3>
        <p>The field which contains holder of different number of features inside the input layer.</p>
        <h3>Balanced by field</h3>
        <p>The value which applied for the assigned by field's unique values polygons. Recommended an area field.</p>
        <h3>Distance treshold</h3>
        <p>The distance range within the grouping for a certain polygon happens. Distance in meter.</p>
        <h3>Tolerance</h3>
        <p>The percent for the balance field.</p>
        <h3>Swap to get</h3>
        <p>The method, with the grouping will be happen.
        Neighbours: Change the neighbours of the seed polygons.
        Closer: Change to get closer the other polygons to the seed polygon.
        Neighbours, then closer: Combinated run, first Neighbours, than Closer function will be run on the results of the Neighbours.
        Closer, then neighbours: Combinated run, first Closer, than Neighbours function will be run on the results of the Closer.
        Hybrid: Combine different approaches: neighbours, distance, shape indicators.</p>
        <h3>Output directory</h3>
        <p>The directory where the outputs will be saved. Two output layers are created, their names are <base layer name> + algorithm name + timeStamp:
        First: Vector layer containing the regrouped polygons 
	    Second: Merged layer containing the merged version of the polygons, the name includes the "merged" tag.</p>
        <h2>Additional parameters</h2>
        <h3>The selected polygons are the only ones used to seed</h3>
        <p>Only works if the "Give preference to selected polygons" parameter is True. If checked, only the selected polygons are used, otherwise the selected and largest polygons together.</p>
        <h3>Use single holding's holders polygons</h3>
        <p>Single-holding polygons are not seeds.</p>
        <h3>Strict neighbourhood method</h3>
        <p>Intend to reduce the average distance and not exceed the existing maximum distance conditions when using any of the Neighbours algorithm methods. This option guarantees the reduction of the polygon distance for each holder.</p>
        <h3>Simplfy</h3>
        <p>Simplified algorithm for processing large datasets. Recommended when the input dataset contains more than 4000 polygons. The simplified algorithm only involves swapping holders between two polygons.</p>
        <h3>Generate statistics</h3>
        <p>Generate statistics about the run: indicators statistics, change logs, holder relations log</p>
        <br></body></html>"""

    def createInstance(self):
        return PolygonGrouper()

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        self.counter = 0
        results = {}
        self.steps = self.calculateSteps(parameters['SwapToGet'])
        feedback = QgsProcessingMultiStepFeedback(self.steps, model_feedback)

        if parameters['OnlySelected'] and parameters['Preference'] is not True:
        # feedback.pushInfo(f"'Only use the selected features' parameters works only with 'Give preference for the selected features parameter'. This parameter is invalided") 
            feedback.pushInfo(f"'Only use the selected features' parameters works only with 'Give preference for the selected features parameter'. 'Give preference for the selected features parameter' is enabled")
            parameters['Preference'] = True

        
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        mainStartTime = time.time()

        #Get inputs
        self.weight = parameters['BalancedByField']
        self.tolerance = parameters['Tolerance']
        self.distance = parameters['DistanceTreshold']
        self.useSingle = parameters['Single']
        self.onlySelected = parameters['OnlySelected']
        self.algorithmIndex = parameters['SwapToGet']
        self.simply = parameters['Simplfy']
        self.strict = parameters['Strict']
        self.stats = parameters['Stats']
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()
       
        self.startLogging(inputLayer, parameters, timeStamp)
        #Create work file and get the starting dictionaries
        tempLayer = self.createTempLayer(inputLayer, parameters["OutputDirectory"], self.algorithmNames[self.algorithmIndex].lower(), timeStamp)
        layer, self.holderAttribute = self.setHolderField(tempLayer, parameters["AssignedByField"])
        self.holderAttributeType, self.holderAttributeLenght = self.getFieldProperties(tempLayer, self.holderAttribute)
        holdersWithHoldings, holdersHoldingNumber = self.getHoldersHoldings(layer)
        layer, self.idAttribute, holdersWithHoldings = self.createIdField(layer, holdersWithHoldings)
        layer.dataProvider().createSpatialIndex()
        holdingsWithArea = self.getHoldingsAreas(layer, parameters["BalancedByField"])
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = self.calculateTotalArea()

        if parameters['Preference']:
            selectedFeatures = self.getSelectedFeatures(inputLayer)
            self.determineSeedPolygons(layer, parameters['Preference'], selectedFeatures)
        else:
            self.determineSeedPolygons(layer)

        feedback.pushInfo('Calculate distance matrix')
        self.distanceMatrix = self.createDistanceMatrix(layer)
        self.filteredDistanceMatrix = self.filterDistanceMatrix(self.distanceMatrix)
        feedback.pushInfo('Distance matrix calculated')

        self.calculateTotalDistances(layer)

        if parameters['Stats']:
            beforeData = self.calculateStatData(layer, self.holderAttribute)
            self.createInteractionOutput()

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            self.endLogging()
            return {}
        #Start one of the functions
        if self.algorithmIndex == 0:
            swapedLayer, totalAreas = self.neighbours(layer, feedback)
        elif self.algorithmIndex == 1:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                swapedLayer, totalAreas = self.closer(layer, feedback)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 2:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                originalSeeds = copy.deepcopy(self.seeds)
                swapedLayer, totalAreas = self.neighbours(layer, feedback)
                swapedLayer, totalAreas = self.closer(swapedLayer, feedback, originalSeeds, totalAreas)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 3:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                swapedLayer, totalAreas = self.closer(layer, feedback)
                swapedLayer, totalAreas = self.neighbours(swapedLayer, feedback, totalAreas)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 4:
            swapedLayer = self.hybrid_method(layer, feedback)
        #Save results and create merged file
        if swapedLayer:
            feedback.setCurrentStep(self.steps-1)

            swapedLayer.commitChanges()
            swapedLayer.removeSelection()
            QgsProject.instance().addMapLayer(swapedLayer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, swapedLayer)

            mergedLayer = self.createMergedFile(swapedLayer, parameters["OutputDirectory"])
            toDeleteAttr = [attr for attr in self.getAttributesNames(mergedLayer) if attr not in self.getAttributesNames(inputLayer)]
            self.cleanMergedLayer(toDeleteAttr, mergedLayer)

            self.copyStyle(inputLayer, swapedLayer)
            self.copyStyle(inputLayer, mergedLayer)

            QgsProject.instance().addMapLayer(mergedLayer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, mergedLayer)

            layer.removeSelection()

            if parameters['Stats']:
                lastHolderAttribute = int(self.actualHolderAttribute.split('_')[0])
                if lastHolderAttribute == self.steps-2:
                    if self.steps-2 >= 10:
                        attributeName = str(lastHolderAttribute) + self.actualHolderAttribute[2:]
                    else:
                        attributeName = str(lastHolderAttribute) + self.actualHolderAttribute[1:]
                else:
                    if lastHolderAttribute >= 10:
                        attributeName = str(lastHolderAttribute-1) + self.actualHolderAttribute[2:]
                    else:
                        attributeName = str(lastHolderAttribute-1) + self.actualHolderAttribute[1:]
                afterData = self.calculateStatData(swapedLayer, attributeName)
                mergedData = self.calculateStatData(mergedLayer, attributeName)
                self.createIndicesStat(beforeData, afterData, mergedData)
                self.createExchangeLog(swapedLayer, attributeName)
                self.saveInteractionOutput()
                self.saveInteractionOutput2(swapedLayer, attributeName)
                #self.saveInteractionOutputGOPA(os.path.join(parameters["OutputDirectory"], f"{str(swapedLayer.source()[:-4])}_interactions.csv"), swapedLayer, attributeName)
                #self.calculateShapeIndexes(swapedLayer, mergedLayer)

            swapedLayer.commitChanges()

            mainEndTime = time.time()
            logging.debug(f'Script time:{mainEndTime-mainStartTime}')

            feedback.setCurrentStep(self.steps)
            self.endLogging()   
            results['OUTPUT'] = swapedLayer
            return results
        else:
            feedback.pushInfo('No change was made!') 
            self.endLogging()   
            return {}

    def startLogging(self, layer, parameters, timeStamp):
        """
        DESCRIPTION: Function to start the logging to a log file
        INPUTS:
                layer: QgsVectorLayer
                parameters: dictionary with the plugin input parameters
                timeStamp: string with the start time of the plugin
        OUTPUTS: None
        """
        path = os.path.join(parameters["OutputDirectory"], f"{str(layer.name())}_log_{timeStamp}.txt")
        formatter = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(filename=path, level=logging.DEBUG, format=formatter, filemode='w')
        logging.debug(f'Start time: {datetime.now().strftime("%Y_%m_%d_%H_%M")}\nInput layer: {parameters["Inputlayer"]}\nPreference to selected items: {parameters["Preference"]}\nUse single holdings holders polygons: {parameters["Single"]}\nHolder atrribute(s): {parameters["AssignedByField"]}\nWeight attribute: {parameters["BalancedByField"]}\nTolerance threshold: {parameters["Tolerance"]}\nDistance threshold: {parameters["DistanceTreshold"]}\nSimplified run: {parameters["Simplfy"]}\nOutput dir: {parameters["OutputDirectory"]}')

    def endLogging(self):
        """
        DESCRIPTION: Function to end the logging
        INPUTS: None
        OUTPUTS: None
        """
        logger = logging.getLogger() 
        handlers = logger.handlers[:]
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        logging.shutdown()

    def calculateSteps(self, algorithmIndex):
        """
        DESCRIPTION: Based on the plugin input, give back the number of the algorithm's steps 
        INPUTS:
                algorithmIndex: Integer, from the plugin input parameter
        OUTPUTS: Integer
        """
        if algorithmIndex == 0:
            return 13
        elif algorithmIndex == 1:
            return 18
        else:
            return 28 

    def getFieldProperties(self, layer, fieldName):
        """
        DESCRIPTION: Give back of a field type and lenght 
        INPUTS:
                layer: QgsVectorLayer
                fieldName: String, name of the field
        OUTPUTS: ogr type, integer
        """
        for field in layer.fields():
            if field.name() == fieldName:
                return field.type(), field.length()

    def getSelectedFeatures(self, inputLayer):
        """
        DESCRIPTION: Give back the selected features of the layer
        INPUTS:
                inputLayer: QgsVectorLayer
        OUTPUTS: QgsVectorLayer
        """
        algParams = {
            'INPUT': inputLayer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selectedFeatures = processing.run("native:saveselectedfeatures",algParams)["OUTPUT"]
        return selectedFeatures

    def createTempLayer(self, layer, directory, postfix, timeStamp=None):
        """
        DESCRIPTION: Create a copy of an input layer
        INPUTS:
                layer: QgsVectorLayer
                directory: String, input parameter, absolute path of the output directory
                postfix: String, postfix for the file name
                timeStamp: String, time stamp to burn into file name, optional
        OUTPUTS: QgsVectorLayer
        """
        if directory:
            if timeStamp:
                path = os.path.join(directory, f"{str(layer.name())}_{postfix}_{timeStamp}.shp")
            else:
                path = os.path.join(directory, f"{str(layer.name())}_{postfix}.shp")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "ESRI Shapefile"
            options.fileEncoding = "UTF-8"
            context = QgsProject.instance().transformContext()
            QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, context, options)
            tempLayer = QgsVectorLayer(path, f"{postfix} layer", "ogr")
            return tempLayer
        else:
            epsg = layer.crs().geographicCrsAuthId()[-4:]
            feats = [feature for feature in layer.getFeatures()]
            memoryLayer = QgsVectorLayer(f"Polygon?crs=epsg:{epsg}", f"{postfix} layer", "memory")
            memoryLayerData = memoryLayer.dataProvider()
            attributeNames = layer.dataProvider().fields().toList()
            memoryLayerData.addAttributes(attributeNames)
            memoryLayer.updateFields()
            memoryLayerData.addFeatures(feats)
            memoryLayer.commitChanges()
            return memoryLayer

    def checkSeedNumber(self, feedback):
        """
        DESCRIPTION: Check every holder seed polygon number. If the number of the seeds for a holder is greater than one, return false
        INPUTS:
                feedback: QgsProcessingMultiStepFeedback
        OUTPUTS: Boolean
        """
        for seed in self.seeds.values():
            if len(seed) > 1:
                feedback.pushInfo('More than one feature preference for one holder at closer function - algorithm stop') 
                return False
        return True

    def setHolderField(self, layer, field):
        """
        DESCRIPTION: Create the holder attribute field
        INPUTS:
                layer: QgsVectorLayer
                field: String, field name
        OUTPUTS: QgsVectorLayer, string
        """
        if len(field) == 1:
            return layer, field[0]
        else:
            layer, fieldName = self.setTempHolderField(layer)
            layer = self.setTempHolderValue(layer, fieldName, field)
            return layer, fieldName

    def setTempHolderField(self, layer):
        """
        DESCRIPTION: Create a new field for the holder
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: QgsVectorLayer, String
        """
        fieldName = 'holder_id'
        layerAttributes = self.getAttributesNames(layer)
        if fieldName in layerAttributes:
            counter = 0
            while fieldName in layerAttributes:
                fieldName = f"{fieldName}{counter}"
                counter += 1
        layer.startEditing()
        dataProvider = layer.dataProvider()
        dataProvider.addAttributes([QgsField(fieldName, QVariant.Int)])
        self.holderAttributeType = QVariant.Int
        self.holderAttributeLenght = -1
        layer.updateFields()
        return layer, fieldName

    def getAttributesNames(self, layer):
        """
        DESCRIPTION: Gets the attribute names of a layer
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: List
        """
        attributes = [field.name() for field in layer.fields()]
        return attributes

    def setTempHolderValue(self, layer, fieldName, attributes):
        """
        DESCRIPTION: Set holder attribute values, if more field has received, create a new field, with a combined Id values
        INPUTS:
                layer: QgsVectorLayer
                fieldName: String, field name
                attributes: List, name of the attribute field, which hold the holder values
        OUTPUTS: QgsVectorLayer
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        fieldNameId = [turn for turn, field in enumerate(layer.fields()) if field.name() == fieldName][0]
        allUniqueCombination = []
        features = layer.getFeatures()
        for turn, feature in enumerate(features):
            tempListWithValues = []
            for attr in attributes:
                value = feature.attribute(attr)
                if value != qgis.core.NULL:
                    tempListWithValues.append(value)
            tempString = ''
            for turn, tempValue in enumerate(tempListWithValues):
                if len(tempListWithValues) > 1:
                    if tempValue != '' and tempValue  != qgis.core.NULL:
                        if tempValue != tempListWithValues[-1]:
                            tempString += f'{tempValue},'
                        else:
                            tempString += f'{tempValue}'
                else:
                    if tempValue and tempValue != '' and tempValue != qgis.core.NULL:
                        tempString += f'{tempValue}'
            if len(tempString) > 0:
                if tempString not in allUniqueCombination:
                    allUniqueCombination.append(tempString)
        counter = 1
        for uniqueString in allUniqueCombination:
            expression = ''
            uniqueValueList = uniqueString.split(',')
            for turn, uniqueValue in enumerate(uniqueValueList):
                if uniqueValue != qgis.core.NULL:
                    if type(uniqueValue) == str:
                        if turn+1 != len(uniqueValueList):
                            expression += '"{field}"=\'{value}\' AND '.format(field=attributes[turn],value=str(uniqueValue))
                        else:
                            expression += '"{field}"=\'{value}\''.format(field=attributes[turn],value=str(uniqueValue))
                    else:
                        if turn+1 != len(uniqueValueList):
                            expression += '"{field}"={value} AND '.format(field=attributes[turn],value=str(uniqueValue))
                        else:
                            expression += '"{field}"={value}'.format(field=attributes[turn],value=str(uniqueValue))
            layer.selectByExpression(expression)
            selectedFeatures = layer.selectedFeatures()
            if layer.selectedFeatureCount() > 0:
                counter += 1
                if (layer.isEditable() == False):
                    layer.startEditing()
                for feature in selectedFeatures:
                    layer.changeAttributeValue(feature.id(), fieldNameId, counter)
                layer.removeSelection()
        layer.commitChanges()

        return layer

    def createIdField(self, layer, holders):
        """
        DESCRIPTION: Create a new field for the holding ids
        INPUTS:
                layer: QgsVectorLayer
                holders: Dictionary, key: holders ids, values: List, holdings ids
        OUTPUTS: QgsVectorLayer, String, Dictionary
        """
        fieldName = 'temp_id'
        layerAttributes = self.getAttributesNames(layer)
        if fieldName in layerAttributes:
            counter = 0
            while fieldName in layerAttributes:
                fieldName = f"{fieldName}{counter}"
                counter += 1
        if (layer.isEditable() == False):
            layer.startEditing()
        dataProvider = layer.dataProvider()
        dataProvider.addAttributes([QgsField(fieldName, QVariant.String, len=10)])
        layer.updateFields()
        layer, holdersWithHoldingId = self.setIdField(layer, fieldName, holders)
        return layer, fieldName, holdersWithHoldingId

    def setIdField(self, layer, attribute, holders):
        """
        DESCRIPTION: Set the holding Id field
        INPUTS:
                layer: QgsVectorLayer
                attribute: String, name of the Id attribute field
                holders: Dictionary, key: holders ids, values: List, holdings ids
        OUTPUTS: QgsVectorLayer, Dictionary
        """
        attributeId = self.getAttributesNames(layer).index(attribute)
        holdersWithHoldingId = {}
        if (layer.isEditable() == False):
            layer.startEditing()
        for holder, holdings in holders.items():
            counter = 0
            for featureId in holdings:
                newId = str(uuid.uuid4())[:10]
                layer.changeAttributeValue(featureId, attributeId, newId)
                counter += 1
                if holder in list(holdersWithHoldingId.keys()):
                    holdersWithHoldingId[holder].append(newId)
                else:
                    holdersWithHoldingId[holder] = list()
                    holdersWithHoldingId[holder].append(newId)
        layer.commitChanges()
        return layer, holdersWithHoldingId

    def getHoldersHoldings(self, layer, holderAttribute=None, attributeName=None):
        """
        DESCRIPTION: Create a dictionary for holder and their holdings
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: Dictionary, key: holders ids, values: List, holdings ids
        """
        holdersWithHoldings = {}
        holdersHoldingNumber = {}
        features = layer.getFeatures()
        for feature in features:
            if attributeName:
                featureId = feature.attribute(attributeName)
            else:
                featureId = feature.id()
            if holderAttribute:
                holder = feature.attribute(holderAttribute)
            else:
                holder = feature.attribute(self.holderAttribute)
            if holder != qgis.core.NULL:
                if holder in list(holdersWithHoldings.keys()):
                    holdersWithHoldings[holder].append(featureId)
                    holdersHoldingNumber[holder] = holdersHoldingNumber[holder] + 1
                else:
                    holdersWithHoldings[holder] = [featureId]
                    holdersHoldingNumber[holder] = 1
        return holdersWithHoldings, holdersHoldingNumber

    def calculateTotalArea(self):
        """
        DESCRIPTION: Create a dictionary for holder total areas
        INPUTS: None
        OUTPUTS: Dictionary, key: holders ids, values: Integer
        """
        holderTotalArea = {}
        for holder, holdings in self.holdersWithHoldings.items():
            totalArea = 0
            for holding in holdings:
                if self.holdingsWithArea[holding] != qgis.core.NULL:
                    totalArea += self.holdingsWithArea[holding]
            holderTotalArea[holder] = totalArea
        return holderTotalArea

    def calculateTotalDistances(self, layer):
        """
        DESCRIPTION: Create two dictionaries for holder total distances and holding distances to seed
        INPUTS: None
        OUTPUTS: 
            Dictionaries, 
                key: holders ids, values: Integer
                key: holding ids, values: Integer
        """
        totalDistances = {}
        holdingWithSeedDistance = {}
        for holder, holdings in self.holdersWithHoldings.items():
            if self.useSingle or self.onlySelected:
                try:
                    seed = self.seeds[holder][0]
                except IndexError:
                    continue
            else:
                seed = self.seeds[holder][0]
            sumDistance = 0
            for holding in holdings:
                try:
                    distance = self.distanceMatrix[seed][holding]
                    sumDistance += distance
                except KeyError:
                    expression = f'"{self.idAttribute}" = \'{seed}\''
                    layer.selectByExpression(expression)
                    featureSeed = layer.selectedFeatures()[0]
                    expression = f'"{self.idAttribute}" = \'{holding}\''
                    layer.selectByExpression(expression)
                    featureTarget = layer.selectedFeatures()[0]
                    # Get geometries of the features
                    geometrySeed = featureSeed.geometry()
                    geometryTarget = featureTarget.geometry()
                    # Calculate the distance
                    distance = geometrySeed.distance(geometryTarget)
                    sumDistance += distance
                holdingWithSeedDistance[holding] = distance
            totalDistances[holder] = sumDistance
        self.totalDistances = totalDistances
        self.holdingWithSeedDistance = holdingWithSeedDistance
        
    def getHoldingsAreas(self, layer, areaId):
        """
        DESCRIPTION: Create a dictionary for holding and its area
        INPUTS:
                layer: QgsVectorLayer
                areaId: Integer, Id of the polygon feature
        OUTPUTS: Dictionary, key: holding id, values: Integer, area
        """
        holdingsWithAreas = {}
        features = layer.getFeatures()
        for feature in features:
            area = feature.attribute(areaId)
            if area == qgis.core.NULL:
                area = feature.geometry().area()/10000
            holdingId = feature.attribute(self.idAttribute)
            holdingsWithAreas[holdingId] = area
        return holdingsWithAreas

    def determineSeedPolygons(self, layer, preference=False, selectedFeatures=None):
        """
        DESCRIPTION: Determine one seed polygon for each holder adn store in a self dictionary
        INPUTS:
                layer: QgsVectorLayer
                preference: Boolean, the selected fature on the input layers will be the seed polygons of their holders
                selectedFeatures: QgsVectorLayer
        OUTPUTS: Dictionary, key: holder id, values: List, holding ids
        """
        holdersWithSeeds = {}
        selectedHolders = []
        if preference:
            algParams = {
                'INPUT': layer,
                'PREDICATE':[3],
                'INTERSECT':selectedFeatures,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            processing.run("native:selectbylocation", algParams)
            selectedFeatures = layer.selectedFeatures()
            for feature in selectedFeatures:
                holderValue = feature.attribute(self.holderAttribute)
                idValue = feature.attribute(self.idAttribute)
                if holderValue not in list(holdersWithSeeds.keys()):
                    holdersWithSeeds[holderValue] = [idValue]
                else:
                    holdersWithSeeds[holderValue].append(idValue)
                selectedHolders.append(holderValue)
            self.selectedHolders = selectedHolders
        for holder, holdings in self.holdersWithHoldings.items():
            if self.onlySelected:
                if holder not in list(holdersWithSeeds.keys()):
                    holdersWithSeeds[holder] = []
            else:
                if holder not in list(holdersWithSeeds.keys()):
                    if holder == 'NULL':
                        holdersWithSeeds[holder] = holdings
                    else:
                        if self.useSingle:
                            if len(holdings) > 1:
                                largestArea = 0
                                largestFeatureId = ''
                                for holding in holdings:
                                    areaValue = self.holdingsWithArea[holding]
                                    if largestArea < areaValue:
                                        largestArea = areaValue
                                        largestFeatureId = holding
                                holdersWithSeeds[holder] = [largestFeatureId]
                            else:
                                holdersWithSeeds[holder] = []
                        else: 
                            largestArea = 0
                            largestFeatureId = ''
                            for holding in holdings:
                                areaValue = self.holdingsWithArea[holding]
                                if largestArea < areaValue:
                                    largestArea = areaValue
                                    largestFeatureId = holding
                            holdersWithSeeds[holder] = [largestFeatureId]
        
        self.seeds = holdersWithSeeds

    def createDistanceMatrix(self, layer):
        """
        DESCRIPTION: Create a distance matrix of the input layer features
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: Dictionary, key: holding id, values: Distionary (nested), key: holding ids, values: Float, distances
        """
        if self.simply:
            nearestPoints = 1000
            """
            epsg = layer.crs().geographicCrsAuthId()[-4:]
            bufferLayer = QgsVectorLayer(f"Polygon?crs=epsg:{epsg}","buffer", "memory")

            for seed in self.seeds:
                expression = f'"{self.idAttribute}" = \'{self.seeds[seed][0]}\''
                layer.selectByExpression(expression)
                selectedFeature = layer.selectedFeatures()
                geomBuffer = selectedFeature[0].geometry().buffer(self.distance+100,-1)
                f = QgsFeature()
                f.setGeometry(geomBuffer)
                bufferLayer.dataProvider().addFeatures([f])      
                features = layer.getFeatures()
                inputs = [f for f in features]
                tempCalculator = 0
                for feat in inputs:
                    if feat.geometry().intersects(geomBuffer):
                        tempCalculator += 1
                if tempCalculator > nearestPoints:
                    nearestPoints = tempCalculator
                layer.removeSelection()
            """
        algParams = {
        'INPUT':layer,
        'ALL_PARTS': False,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        centroids = processing.run("native:centroids", algParams)['OUTPUT']
        algParams = {
        'INPUT':centroids,
        'INPUT_FIELD': self.idAttribute,
        'TARGET': centroids,
        'TARGET_FIELD': self.idAttribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        if self.simply:
            algParams['MATRIX_TYPE'] = 0
            algParams['NEAREST_POINTS'] = nearestPoints
        matrix = processing.run("qgis:distancematrix", algParams)['OUTPUT']
        if self.simply:
            distanceMatrix = {}
            names = self.getAttributesNames(matrix)
            features = matrix.getFeatures()
            for feature in features:
                featureId = feature.attribute('InputID')
                targetId = feature.attribute('TargetID')
                distance = feature.attribute('Distance')
                if featureId in list(distanceMatrix.keys()):
                    distanceMatrix[featureId][targetId] = distance
                else:
                    distanceMatrix[featureId] = {}
                    distanceMatrix[featureId][targetId] = distance
        else:
            distanceMatrix = {}
            names = self.getAttributesNames(matrix)
            features = matrix.getFeatures()
            for feature in features:
                tempDict = {}
                for field in names:
                    value = feature.attribute(field)
                    tempDict[field] = value
                distanceMatrix[feature.attribute('ID')] = tempDict

        return distanceMatrix

    def filterDistanceMatrix(self, distanceMatrix):
        """
        DESCRIPTION: Filter a distance matrix based on the distance threshold 
        INPUTS:
                distanceMatrix: Dictionary, distance matrix
        OUTPUTS: Dictionary, key: holding id, values: Distionary (nested), key: holding ids, values: Float, distances
        """
        filteredMatrix = {}
        for key, value in distanceMatrix.items():
            sortedDistances = [(y, x) for y, x in zip(list(value.values()), list(value.keys())) if x != 'ID']
            sortedDistances.sort()
            subFilteredMatrix = {}
            for value2, key2 in sortedDistances:
                if value2 <= self.distance:
                    subFilteredMatrix[key2] = value2
                else:
                    break
            filteredMatrix[key] = subFilteredMatrix
        return filteredMatrix

    def getChangableHoldings(self, inDistance=None):
        """
        DESCRIPTION: Get a list, which can be used for changes (not a seed)
        INPUTS:
                inDistance: List, holding ids, which are inside the distance threshold
        OUTPUTS: List, holding ids
        """
        changableHoldings = []
        if inDistance:
            for holding in inDistance:
                listedSeeds = [seed for seedList in list(self.seeds.values()) for seed in seedList]
                if holding not in listedSeeds:
                    changableHoldings.append(holding)
        else:
            for holder, holdings in self.holdersWithHoldings.items():
                for holding in holdings:
                    if holding not in self.seeds[holder]:
                        changableHoldings.append(holding)
        return changableHoldings

    def getNeighbours(self, layer, seed):
        """
        DESCRIPTION: Get neighbours holdings of a certain polygon
        INPUTS:
                layer: QgsVectorLayer
                seed: holding id of the holder's seed polygon
        OUTPUTS:
                neighboursIds: List, holding ids
                neighbours: QgsVectorLayer
        """
        expression = f'"{self.idAttribute}" = \'{seed}\''
        layer.selectByExpression(expression)
        algParams = {
            'INPUT': layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        seedFeatures = processing.run('native:saveselectedfeatures', algParams)["OUTPUT"]
        algParams = {
            'INPUT': layer,
            'INTERSECT': seedFeatures,
            'PREDICATE': 4,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        neighbours = processing.run('native:extractbylocation', algParams)["OUTPUT"]
        layer.removeSelection()

        neighboursFeatures = neighbours.getFeatures()
        neighboursIds = [neighboursFeature.attribute(self.idAttribute) for neighboursFeature in neighboursFeatures]

        return neighboursIds, neighbours

    def idsForChange(self, holdingList, changables):
        """
        DESCRIPTION: Get holding ids, which are on the changables list
        INPUTS:
                holdingList: List, holding ids
                changables: List, holding ids
        OUTPUTS:
                ids: List, holding ids
        """
        try:
            ids = []
            for holdingId in holdingList:
                if holdingId in changables:
                    ids.append(holdingId)
            return ids
        except ValueError:
            return None

    def neighbours(self, layer, feedback, totalAreas=None):
        """
        DESCRIPTION: Function for make swap to get group holder's holdings around their seed polygons. The function try to swap the neighbour polygons for other holdings of the holder. 
        INPUTS:
                layer: QgsVectorLayer
                feedback: QgsProcessingMultiStepFeedback
        OUTPUTS: QgsVectorLayer
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        changes = 1
        changer = True
        self.globalChangables = self.getChangableHoldings()

        try:
            turn = int(self.actualHolderAttribute.split('_')[0])
            if self.algorithmIndex == 3:
                if turn == (self.steps/2)-3:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
                else:
                    if turn >= 10:
                        self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                        self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                    else:
                        self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                        self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                    turn -= 1
        except AttributeError:
            turn = 0

        if totalAreas:
            holdersLocalTotalArea = totalAreas
        else:
            holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
        feedback.pushInfo('Neighbours algorithm start')

        while changer:
            turn += 1
            layer = self.setTurnAttributes(layer, turn)
            not_changables = []
            feedback.pushInfo(f'Turn {turn}')
            for holder, holdings in self.holdersWithHoldings.items():
                if holder != 'NULL':
                    seeds = self.seeds[holder]
                    if len(seeds) >= 1:
                        for seed in seeds:
                            #feedback.info(seed)
                            neighboursIds, neighboursLayer = self.getNeighbours(layer, seed)
                            inDistance = self.filteredDistanceMatrix[seed]
                            distanceChanges = self.getChangableHoldings(inDistance)
                            localChangables = [distance for distance in distanceChanges if distance in self.globalChangables and distance not in not_changables]
                            if len(localChangables) > 0:
                                holdingsIds = []
                                changesIds = []
                                
                                for holdingId in holdings:
                                    if holdingId in neighboursIds:
                                        if holdingId not in self.seeds[holder]:
                                            self.seeds[holder].append(holdingId)
                                    else:
                                        holdingsIds.append(holdingId)

                                neighboursFeatures = neighboursLayer.getFeatures()
                                for nghfeat in neighboursFeatures:
                                    # Get holder total area
                                    holderTotalArea = holdersLocalTotalArea[holder]
                                    #Filter holdings
                                    filteredHolderHoldingsIds = self.idsForChange(holdingsIds, localChangables)
                                    if filteredHolderHoldingsIds:
                                        # Get ngh holder name
                                        neighbourHolder = nghfeat.attribute(self.actualHolderAttribute)
                                        if neighbourHolder != 'NULL' and neighbourHolder != holder:
                                            try:
                                                targetHolderSeed = self.seeds[neighbourHolder][0]
                                            except IndexError:
                                                if self.useSingle:
                                                    targetHolderSeed = False
                                                else:
                                                    continue
                                            except KeyError:
                                                try:
                                                    neighbourHolder = int(neighbourHolder)
                                                    targetHolderSeed = self.seeds[int(neighbourHolder)][0]
                                                except KeyError:
                                                    if self.useSingle:
                                                        targetHolderSeed = False
                                                    else:
                                                        continue
                                            # Get holder total area
                                            neighbourHolderTotalArea = holdersLocalTotalArea[neighbourHolder]
                                            # Get holders holdings
                                            neighbourHoldings = self.holdersWithHoldings[neighbourHolder]
                                            # Filter holdings
                                            neighbourHoldingsIds = self.idsForChange(neighbourHoldings, localChangables)
                                            if neighbourHoldingsIds:
                                                # Filter out nghs
                                                filteredNeighbourHoldingsIds = [holdingId for holdingId in neighbourHoldingsIds if holdingId not in neighboursIds]

                                                neighbourTargetFeatureId = nghfeat.attribute(self.idAttribute)
                                                if neighbourTargetFeatureId in localChangables:
                                                    if not targetHolderSeed:
                                                        neighbourHoldingsCombinations = [[neighbourTargetFeatureId]]
                                                    else: 
                                                        neighbourHoldingsCombinations = self.combine_with_constant_in_all(filteredNeighbourHoldingsIds, neighbourTargetFeatureId)
                                                    holderCombinationForChange = None
                                                    neighbourCombinationForChange = None
                                                    lenTurn = 0
                                                    holderNewTotalArea = 0
                                                    neighbourNewTotalArea = 0
                                                    totalAreaDifference = -999
                                                    for combinationLenght in range(1,len(filteredHolderHoldingsIds) + 1):
                                                        if combinationLenght <= 10:
                                                            combTurn = 0
                                                            for combination in itertools.combinations(filteredHolderHoldingsIds, combinationLenght):
                                                                if self.simply:
                                                                    if combTurn < 10000*combinationLenght and lenTurn < 20000:
                                                                        lenTurn += 1
                                                                        temporaryHolderArea = self.calculateCombinationArea(combination)
                                                                        for neighbourCombination in neighbourHoldingsCombinations:
                                                                            if self.strict:
                                                                                #Distance conditions
                                                                                if self.useSingle and not targetHolderSeed:
                                                                                    holderMaxDistance = self.maxDistance(combination, seed, layer)
                                                                                    holderAvgDistanceOld = self.avgDistance(combination, seed, layer)
                                                                                    holderAvgDistanceNew = self.avgDistance(neighbourCombination, seed, layer)
                                                                                    targetCloser = True
                                                                                    targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                                                                    holderCloser = self.isCloser(targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
                                                                                else:
                                                                                    targetMaxDistance = self.maxDistance(neighbourCombination, targetHolderSeed, layer)
                                                                                    targetAvgDistanceOld = self.avgDistance(neighbourCombination, targetHolderSeed, layer)
                                                                                    holderMaxDistance = self.maxDistance(combination, seed, layer)
                                                                                    holderAvgDistanceOld = self.avgDistance(combination, seed, layer)
                                                                                    holderAvgDistanceNew = self.avgDistance(neighbourCombination, seed, layer)
                                                                                    targetAvgDistanceNew = self.avgDistance(combination, targetHolderSeed, layer)
                                                                                    targetCloser = self.isCloser(holderMaxDistance, neighbourCombination, seed, holder)
                                                                                    holderCloser = self.isCloser(targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
                                                                            else:
                                                                                targetCloser, holderCloser = True, True
                                                                                targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                                                                holderAvgDistanceNew, holderAvgDistanceOld = 1,2
                                                                            if targetCloser and holderCloser:
                                                                                if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                                                                                    #Weight condition
                                                                                    temporaryTargetArea = self.calculateCombinationArea(neighbourCombination)
                                                                                    newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                                                                                    newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                                                                                    thresholdHolder = self.checkTotalAreaThreshold(newHolderTotalArea, holder)
                                                                                    thresholdNeighbour = self.checkTotalAreaThreshold(newNeighbourTotalArea, neighbourHolder)
                                                                                    difference = abs(newHolderTotalArea-holderTotalArea)
                                                                                    if totalAreaDifference == -999:
                                                                                        if thresholdHolder and thresholdNeighbour:
                                                                                            holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                                                            targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                                                            if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[neighbourHolder]:
                                                                                                holderCombinationForChange = combination
                                                                                                neighbourCombinationForChange = neighbourCombination
                                                                                                holderNewTotalArea = newHolderTotalArea
                                                                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                                                                totalAreaDifference = difference
                                                                                                combTurn += 1
                                                                                    else:
                                                                                        if thresholdHolder and thresholdNeighbour and difference < totalAreaDifference:
                                                                                            holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                                                            targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                                                            if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[neighbourHolder]:
                                                                                                holderCombinationForChange = combination
                                                                                                neighbourCombinationForChange = neighbourCombination
                                                                                                holderNewTotalArea = newHolderTotalArea
                                                                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                                                                totalAreaDifference = difference
                                                                                                combTurn += 1                               
                                                                    else:
                                                                        break
                                                                else:
                                                                    temporaryHolderArea = self.calculateCombinationArea(combination)
                                                                    for neighbourCombination in neighbourHoldingsCombinations:
                                                                        if self.strict:
                                                                            #Distance conditions
                                                                            if self.useSingle and not targetHolderSeed:
                                                                                holderMaxDistance = self.maxDistance(combination, seed, layer)
                                                                                holderAvgDistanceOld = self.avgDistance(combination, seed, layer)
                                                                                holderAvgDistanceNew = self.avgDistance(neighbourCombination, seed, layer)
                                                                                targetCloser = True
                                                                                targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                                                                holderCloser = self.isCloser(targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
                                                                            else: 
                                                                                targetMaxDistance = self.maxDistance(neighbourCombination, targetHolderSeed, layer)
                                                                                targetAvgDistanceOld = self.avgDistance(neighbourCombination, targetHolderSeed, layer)
                                                                                holderMaxDistance = self.maxDistance(combination, seed, layer)
                                                                                holderAvgDistanceOld = self.avgDistance(combination, seed, layer)
                                                                                holderAvgDistanceNew = self.avgDistance(neighbourCombination, seed, layer)
                                                                                targetAvgDistanceNew = self.avgDistance(combination, targetHolderSeed, layer)
                                                                                targetCloser = self.isCloser(holderMaxDistance, neighbourCombination, seed, holder)
                                                                                holderCloser = self.isCloser(targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
                                                                        else:
                                                                            targetCloser, holderCloser = True, True
                                                                            targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                                                            holderAvgDistanceNew, holderAvgDistanceOld = 1,2
                                                                        if targetCloser and holderCloser:
                                                                            if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                                                                                #Weight condition
                                                                                temporaryTargetArea = self.calculateCombinationArea(neighbourCombination)
                                                                                newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                                                                                newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                                                                                thresholdHolder = self.checkTotalAreaThreshold(newHolderTotalArea, holder)
                                                                                thresholdNeighbour = self.checkTotalAreaThreshold(newNeighbourTotalArea, neighbourHolder)
                                                                                difference = abs(newHolderTotalArea-holderTotalArea)
                                                                                if totalAreaDifference == -999:
                                                                                    if thresholdHolder and thresholdNeighbour:
                                                                                        holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                                                        targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                                                        if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[neighbourHolder]:
                                                                                            holderCombinationForChange = combination
                                                                                            neighbourCombinationForChange = neighbourCombination
                                                                                            holderNewTotalArea = newHolderTotalArea
                                                                                            neighbourNewTotalArea = newNeighbourTotalArea
                                                                                            totalAreaDifference = difference
                                                                                else:
                                                                                    if thresholdHolder and thresholdNeighbour and difference < totalAreaDifference:
                                                                                        holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                                                        targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                                                        if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[neighbourHolder]:
                                                                                            holderCombinationForChange = combination
                                                                                            neighbourCombinationForChange = neighbourCombination
                                                                                            holderNewTotalArea = newHolderTotalArea
                                                                                            neighbourNewTotalArea = newNeighbourTotalArea
                                                                                            totalAreaDifference = difference

                                                    if holderCombinationForChange and neighbourCombinationForChange:
                                                        self.setAttributeValues(layer, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                                                        if self.stats:
                                                            self.interactionTable[holder][neighbourHolder] += 1
                                                            self.interactionTable[neighbourHolder][holder] += 1
                                                        if len(holderCombinationForChange) > 1 and len(neighbourCombinationForChange) > 1:
                                                            #many to many change
                                                            for holding in holderCombinationForChange:
                                                                localChangables.pop(localChangables.index(holding))
                                                                changesIds.append(holding)
                                                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holding]
                                                                self.holdingWithSeedDistance[holding] = self.distanceMatrix[targetHolderSeed][holding]
                                                                self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[holding]
                                                            for ngh in neighbourCombinationForChange:
                                                                localChangables.pop(localChangables.index(ngh))
                                                                changesIds.append(ngh)
                                                                self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[ngh]
                                                                self.holdingWithSeedDistance[ngh] = self.distanceMatrix[seed][ngh]
                                                                self.totalDistances[holder] += self.holdingWithSeedDistance[ngh]
                                                            self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                                            self.seeds[holder].append(neighbourTargetFeatureId)
                                                            holdersLocalTotalArea[holder] = holderNewTotalArea
                                                            holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                                            commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                                        elif len(holderCombinationForChange) > 1 and len(neighbourCombinationForChange) == 1 or len(holderCombinationForChange) == 1 and len(neighbourCombinationForChange) > 1:
                                                            #many to one change
                                                            if len(holderCombinationForChange) > 1:
                                                                for hold in holderCombinationForChange:
                                                                    localChangables.pop(localChangables.index(hold))
                                                                    changesIds.append(hold)
                                                                    self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold] 
                                                                    if targetHolderSeed:
                                                                        self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                                                                    else:
                                                                        self.holdingWithSeedDistance[hold] = self.distanceMatrix[neighbourTargetFeatureId][hold]
                                                                    if targetHolderSeed:
                                                                        self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[hold]
                                                                    else:
                                                                        if self.useSingle:
                                                                            try:
                                                                                self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[hold]
                                                                            except KeyError:
                                                                                self.totalDistances[neighbourHolder] = 0
                                                                                self.totalDistances[neighbourHolder] = self.holdingWithSeedDistance[hold]
                                                                localChangables.pop(localChangables.index(neighbourTargetFeatureId))
                                                                changesIds.append(neighbourTargetFeatureId)
                                                                if targetHolderSeed:
                                                                    self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                                                else:
                                                                    if self.useSingle:
                                                                        self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                                                self.holdingWithSeedDistance[neighbourTargetFeatureId] = self.distanceMatrix[seed][neighbourTargetFeatureId]
                                                                self.totalDistances[holder] += self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                                            else:
                                                                for ngh in neighbourCombinationForChange:
                                                                    localChangables.pop(localChangables.index(ngh))
                                                                    changesIds.append(ngh)
                                                                    self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[ngh] 
                                                                    self.holdingWithSeedDistance[ngh] = self.distanceMatrix[seed][ngh]
                                                                    self.totalDistances[holder] += self.holdingWithSeedDistance[ngh]
                                                                localChangables.pop(localChangables.index(holderCombinationForChange[0]))
                                                                changesIds.append(holderCombinationForChange[0])
                                                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holderCombinationForChange[0]] 
                                                                self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                                                self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[holderCombinationForChange[0]]
                                                            self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                                            self.seeds[holder].append(neighbourTargetFeatureId)
                                                            holdersLocalTotalArea[holder] = holderNewTotalArea
                                                            holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                                            commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                                        else:
                                                            #one to one change
                                                            localChangables.pop(localChangables.index(neighbourTargetFeatureId))
                                                            localChangables.pop(localChangables.index(holderCombinationForChange[0]))
                                                            changesIds.append(holderCombinationForChange[0])
                                                            changesIds.append(neighbourTargetFeatureId)
                                                            self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                                            self.seeds[holder].append(neighbourTargetFeatureId)
                                                            self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holderCombinationForChange[0]] + self.distanceMatrix[seed][neighbourTargetFeatureId]
                                                            if targetHolderSeed:
                                                                self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId] + self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                                            self.holdingWithSeedDistance[neighbourTargetFeatureId] = self.distanceMatrix[seed][neighbourTargetFeatureId]
                                                            if targetHolderSeed:
                                                                self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                                            else:
                                                                if self.useSingle:
                                                                    self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[neighbourTargetFeatureId][holderCombinationForChange[0]]
                                                            holdersLocalTotalArea[holder] = holderNewTotalArea
                                                            holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                                            commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                not_changables.extend(changesIds)
                    elif len(seeds) == 0:
                        continue
            if turn == 1:
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
                if changes == 0:
                    changer = False
                else:
                    changes = copy.deepcopy(self.counter)
            elif self.algorithmIndex == 3 and changes == 1 and turn <= (self.steps/2)-2:
                changes = copy.deepcopy(self.counter)
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter-changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter-changes}')
                if changes == self.counter:
                    changer = False
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                    indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                    layer.commitChanges()
                elif (self.algorithmIndex == 0 or self.algorithmIndex == 3) and (turn == self.steps-3):
                    changer = False
                elif self.algorithmIndex == 2 and turn == (self.steps/2)-3:
                    changer = False
                else:
                    changes = copy.deepcopy(self.counter)
            feedback.setCurrentStep(1 + turn)
            feedback.pushInfo(f'Save turn results to the file')
            if feedback.isCanceled():
                return {}
        return layer, holdersLocalTotalArea

    def closer(self, layer, feedback, seeds=None, totalAreas=None):
        """
        DESCRIPTION: Function for make swap to get group holder's holdings closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
        INPUTS:
                layer: QgsVectorLayer
                feedback: QgsProcessingMultiStepFeedback
                seeds: List, holding ids
        OUTPUTS: QgsVectorLayer
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        changes = 1
        changer = True

        if seeds:
            self.seeds = seeds
            turn = int(self.actualHolderAttribute.split('_')[0])
            if self.algorithmIndex == 2:
                if turn == (self.steps/2)-3:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
                else:
                    if turn >= 10:
                        self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                        self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                    else:
                        self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                        self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                    turn -= 1
        else:
            self.globalChangables = self.getChangableHoldings()
            turn = 0

        if totalAreas:
            holdersLocalTotalArea = totalAreas
        else:
            holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
        feedback.pushInfo('Closer algorithm started')

        while changer:
            turn += 1
            layer = self.setTurnAttributes(layer, turn)
            localHoldersWithHoldings = copy.deepcopy(self.holdersWithHoldings)
            localChangables = copy.deepcopy(self.globalChangables)
            feedback.pushInfo(f'Turn {turn}')
            for holder, holdings in localHoldersWithHoldings.items():
                if holder != 'NULL':
                    holderTotalArea = holdersLocalTotalArea[holder]
                    seedList = self.seeds[holder]
                    if len(seedList) == 1:
                        seed = seedList[0]
                    else:
                        continue
                    # List of all holdings, with distance to the current seed
                    inDistance = self.filteredDistanceMatrix[seed]
                    # List of holdings, which are not seeds, with distance to the current seed
                    distanceChanges = self.getChangableHoldings(inDistance)
                    # List of holder's holdings, which are suitable for change
                    filteredHolderHoldingsIds = self.idsForChange(holdings, distanceChanges)
                    filteredHolderHoldingsIds = self.idsForChange(filteredHolderHoldingsIds, localChangables)
                    filteredHolderHoldingsIds = self.idsForChange(filteredHolderHoldingsIds, self.holdersWithHoldings[holder])
                    sortedDistances = [(y, x) for y, x in zip(list(inDistance.values()), list(inDistance.keys())) if x in filteredHolderHoldingsIds]
                    sortedDistances.sort()
                    filteredHolderHoldingsIds = [key for value, key in sortedDistances[:5]]
                    minAreaHolding = min([self.holdingsWithArea[hold] for hold in holdings])

                    holderHoldingsCombinations = self.combine_with_constant_in_all(filteredHolderHoldingsIds)

                    tempHolderCombination = None
                    tempTargetCombination = None
                    tempHolderTotalArea = None
                    tempTargetTotalArea = None
                    targetHolder = None
                    measure = None

                    filteredLocalChangables = []
                    for distance in distanceChanges:
                        if distance not in filteredHolderHoldingsIds and distance in localChangables:
                            filteredLocalChangables.append(distance)

                    targetHolders = []
                    for changable in filteredLocalChangables:
                        for allHolder, allHoldings in self.holdersWithHoldings.items():
                            if changable in allHoldings and allHolder not in targetHolders and allHolder != 'NULL' and holdersLocalTotalArea[allHolder] > minAreaHolding:
                                targetHolders.append(allHolder)
                    if self.simply:
                        if len(targetHolders) > 50:
                            targetHolders = random.choices(targetHolders, k=50)

                    for tempTargetHolder in targetHolders:
                        targetHolderSeed = self.seeds[tempTargetHolder]
                        if len(targetHolderSeed) > 0:
                            targetHolderSeed = self.seeds[tempTargetHolder][0]
                            filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                            hold in filteredLocalChangables and hold != targetHolderSeed]
                            
                            targetAllCombinations = self.combine_with_constant_in_all(filteredLocalTargetHoldings)
                            targetCombinations = []
                            originCombinations = []
                            combTurn = 0
                            for sortedCombination in targetAllCombinations:
                                if self.simply:
                                    if combTurn < 200000:
                                        combTurn += 1
                                    else:
                                        break
                                targetMaxDistance = self.maxDistance(sortedCombination, targetHolderSeed, layer)
                                targetAvgDistanceOld = self.avgDistance(sortedCombination, targetHolderSeed, layer)
                                for holderCombination in holderHoldingsCombinations:
                                    holderMaxDistance = self.maxDistance(holderCombination, seed, layer)
                                    holderAvgDistanceOld = self.avgDistance(holderCombination, seed, layer)
                                    holderAvgDistanceNew = self.avgDistance(sortedCombination, seed, layer)
                                    targetAvgDistanceNew = self.avgDistance(holderCombination, targetHolderSeed, layer)
                                    targetCloser = self.isCloser(holderMaxDistance, sortedCombination, seed, holder)
                                    holderCloser = self.isCloser(targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder)
                                    if targetCloser and holderCloser:
                                        if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                                            targetCombinations.append(sortedCombination)
                                            originCombinations.append(holderCombination)                                     

                            if len(originCombinations) > 0 and len(targetCombinations) > 0:
                                for turned, targetCombination in enumerate(targetCombinations):
                                    holderCombination = originCombinations[turned]
                                    newHolderTotalArea = holderTotalArea - self.calculateCombinationArea(holderCombination) + self.calculateCombinationArea(targetCombination)
                                    if self.checkTotalAreaThreshold(newHolderTotalArea, holder):
                                        newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - self.calculateCombinationArea(targetCombination) + self.calculateCombinationArea(holderCombination)
                                        if self.checkTotalAreaThreshold(newTargetTotalArea, tempTargetHolder):
                                            localMeasure = sum([self.calculateCompositeNumber(seed, tempId) for tempId in holderCombination])
                                            holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(holderCombination) + len(targetCombination)
                                            targetNewHoldingNum = self.holdersHoldingNumber[tempTargetHolder] - len(targetCombination) + len(holderCombination)
                                            if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[tempTargetHolder]:
                                                if not measure:
                                                    targetHolder = copy.copy(tempTargetHolder)
                                                    tempHolderCombination = copy.copy(holderCombination)
                                                    tempTargetCombination = copy.copy(targetCombination)
                                                    measure = copy.copy(localMeasure)
                                                    tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                                    tempTargetTotalArea = copy.copy(newTargetTotalArea)
                                                else:
                                                    if measure < localMeasure:
                                                        targetHolder = copy.copy(tempTargetHolder)
                                                        tempHolderCombination = copy.copy(holderCombination)
                                                        tempTargetCombination = copy.copy(targetCombination)
                                                        measure = copy.copy(localMeasure)
                                                        tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                                        tempTargetTotalArea = copy.copy(newTargetTotalArea)
                        else:
                            if self.useSingle:
                                if self.onlySelected:
                                    if tempTargetHolder not in self.selectedHolders:
                                        continue
                                filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                                hold in filteredLocalChangables]
                                
                                targetAllCombinations = self.combine_with_constant_in_all(filteredLocalTargetHoldings)
                                targetCombinations = []
                                originCombinations = []
                                combTurn = 0
                                for sortedCombination in targetAllCombinations:
                                    if self.simply:
                                        if combTurn < 200000:
                                            combTurn += 1
                                        else:
                                            break
                                    for holderCombination in holderHoldingsCombinations:
                                        holderMaxDistance = self.maxDistance(holderCombination, seed, layer)
                                        holderAvgDistanceOld = self.avgDistance(holderCombination, seed, layer)
                                        holderAvgDistanceNew = self.avgDistance(sortedCombination, seed, layer)
                                        targetCloser = self.isCloser(holderMaxDistance, sortedCombination, seed, holder)
                                        holderCloser = True
                                        if targetCloser and holderCloser:
                                            if holderAvgDistanceNew < holderAvgDistanceOld:
                                                targetCombinations.append(sortedCombination)
                                                originCombinations.append(holderCombination)                                     

                                if len(originCombinations) > 0 and len(targetCombinations) > 0:
                                    for turned, targetCombination in enumerate(targetCombinations):
                                        holderCombination = originCombinations[turned]
                                        newHolderTotalArea = holderTotalArea - self.calculateCombinationArea(holderCombination) + self.calculateCombinationArea(targetCombination)
                                        if self.checkTotalAreaThreshold(newHolderTotalArea, holder):
                                            newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - self.calculateCombinationArea(targetCombination) + self.calculateCombinationArea(holderCombination)
                                            if self.checkTotalAreaThreshold(newTargetTotalArea, tempTargetHolder):
                                                localMeasure = sum([self.calculateCompositeNumber(seed, tempId) for tempId in holderCombination])
                                                holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(holderCombination) + len(targetCombination)
                                                targetNewHoldingNum = self.holdersHoldingNumber[tempTargetHolder] - len(targetCombination) + len(holderCombination)
                                                if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[tempTargetHolder]:
                                                    if not measure:
                                                        targetHolder = copy.copy(tempTargetHolder)
                                                        tempHolderCombination = copy.copy(holderCombination)
                                                        tempTargetCombination = copy.copy(targetCombination)
                                                        measure = copy.copy(localMeasure)
                                                        tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                                        tempTargetTotalArea = copy.copy(newTargetTotalArea)
                                                    else:
                                                        if measure < localMeasure:
                                                            targetHolder = copy.copy(tempTargetHolder)
                                                            tempHolderCombination = copy.copy(holderCombination)
                                                            tempTargetCombination = copy.copy(targetCombination)
                                                            measure = copy.copy(localMeasure)
                                                            tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                                            tempTargetTotalArea = copy.copy(newTargetTotalArea)

                    if measure:
                        targetHolderSeed = self.seeds[targetHolder]
                        if len(targetHolderSeed) > 0:
                            targetHolderSeed = self.seeds[targetHolder][0]
                        elif len(targetHolderSeed) == 0:
                            if self.useSingle:
                                targetHolderSeed = filteredLocalTargetHoldings[0]
                        tempHolderCombination = list(tempHolderCombination)
                        tempTargetCombination = list(tempTargetCombination)
                        self.setAttributeValues(layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                        if self.stats:
                            self.interactionTable[holder][targetHolder] += 1
                            self.interactionTable[targetHolder][holder] += 1
                        if len(tempHolderCombination) > 1 and len(tempTargetCombination) > 1:
                            #many to many change
                            for hold in tempHolderCombination:
                                localChangables.pop(localChangables.index(hold))
                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold]
                                self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                                self.totalDistances[targetHolder] += self.holdingWithSeedDistance[hold]
                            for ch in tempTargetCombination:
                                localChangables.pop(localChangables.index(ch))
                                self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch]
                                self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                                self.totalDistances[holder] += self.holdingWithSeedDistance[ch]
                            holdersLocalTotalArea[holder] = tempHolderTotalArea
                            holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                            commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)
                        elif len(tempHolderCombination) > 1 and len(tempTargetCombination) == 1 or len(tempHolderCombination) == 1 and len(tempTargetCombination) > 1:
                            #many to one change
                            if len(tempHolderCombination) > 1:
                                for hold in tempHolderCombination:
                                    localChangables.pop(localChangables.index(hold))
                                    self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold]
                                    if targetHolderSeed:
                                        self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                                    else:
                                        if self.useSingle:
                                            self.holdingWithSeedDistance[hold] = self.distanceMatrix[tempTargetCombination[0]][hold]
                                    self.totalDistances[targetHolder] += self.holdingWithSeedDistance[hold]
                                localChangables.pop(localChangables.index(tempTargetCombination[0]))
                                self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]]
                                self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                                self.totalDistances[holder] += self.holdingWithSeedDistance[tempTargetCombination[0]]
                                commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination[0]}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            else:
                                for ch in tempTargetCombination:
                                    localChangables.pop(localChangables.index(ch))
                                    self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch] 
                                    self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                                    self.totalDistances[holder] += self.holdingWithSeedDistance[ch]
                                localChangables.pop(localChangables.index(tempHolderCombination[0]))
                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]]
                                self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                                self.totalDistances[targetHolder] += self.holdingWithSeedDistance[tempHolderCombination[0]]
                                commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            holdersLocalTotalArea[holder] = tempHolderTotalArea
                            holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                        else:
                            #one to one change
                            localChangables.pop(localChangables.index(tempTargetCombination[0]))
                            localChangables.pop(localChangables.index(tempHolderCombination[0]))
                            self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]] + self.distanceMatrix[seed][tempTargetCombination[0]]
                            if targetHolderSeed:
                                self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]] + self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                            self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                            if targetHolderSeed:
                                self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                            else:
                                if self.useSingle:
                                    self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[tempTargetCombination[0]][tempHolderCombination[0]]
                            holdersLocalTotalArea[holder] = tempHolderTotalArea
                            holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                            commitMessage = f'Change {str(self.counter)} for {tempTargetCombination[0]} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination[0]}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)
                if feedback.isCanceled():
                    self.endLogging()
                    return {}

            if feedback.isCanceled():
                self.endLogging() 
                return {}
            if turn == 1:
                logging.debug(f'Changes in turn {turn}: {self.counter}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                if self.counter == 0:
                    changer = False
                    return None
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filterTouchinFeatures(layer) 
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
                if changes == self.counter:
                    changer = False
                    if self.algorithmIndex != 3:
                        layer.startEditing()
                        indexes = []
                        indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                        indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                        layer.deleteAttributes(indexes)
                        layer.updateFields()
                        layer.commitChanges()
                elif (self.algorithmIndex == 1 or self.algorithmIndex == 2) and (turn == self.steps-3):
                    self.filterTouchinFeatures(layer)
                    changer = False
                elif self.algorithmIndex == 3 and turn == (self.steps/2)-3:
                    self.filterTouchinFeatures(layer)
                    changer = False
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filterTouchinFeatures(layer)
            feedback.setCurrentStep(1+turn)
            feedback.pushInfo('Save turn results to the file')

        return layer, holdersLocalTotalArea

    def createNewAttribute(self, layer, turn, adj, typer=QVariant.String, lenght=50):
        """
        DESCRIPTION: Create new attribute field in a layer
        INPUTS:
                layer: QgsVectorLayer
                turn: Integer
                adj: String
                typer: QVariant
                lenght: Integer
        OUTPUTS: 
                layer: QgsVectorLayer
                fieldName: String
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        fieldName = f'{turn}_{adj}'
        layerAttributes = self.getAttributesNames(layer)
        if fieldName in layerAttributes:
            counter = 0
            while fieldName in layerAttributes:
                fieldName = f"{fieldName}{counter}"
                counter += 1
        layer.startEditing()
        dataProvider = layer.dataProvider()
        if typer == QVariant.Int:
            dataProvider.addAttributes([QgsField(fieldName, typer)])
        else:
            dataProvider.addAttributes([QgsField(fieldName, typer, len=lenght)])
        layer.updateFields()
        layer.commitChanges()
        return layer, fieldName

    def setNewAttribute(self, layer, featureId, newValue, field):
        """
        DESCRIPTION: Set the value of a field in a layer
        INPUTS:
                layer: QgsVectorLayer
                featureId: Integer
                newValue: String or Integer
                field: String, attribute name
        OUTPUTS: None
        """
        expression = ''
        expression += f'"{self.idAttribute}" = \'{featureId}\''
        layer.selectByExpression(expression)
        index = self.getAttributesNames(layer).index(field)
        layer.startEditing()
        for feature in layer.selectedFeatures():
            layer.changeAttributeValue(feature.id(), index, newValue)
        layer.commitChanges()

    def setTurnAttributes(self, layer, turn):
        """
        DESCRIPTION: Set the values of a fields in a layer
        INPUTS:
                layer: QgsVectorLayer
                turn: Integer
        OUTPUTS: QgsVectorLayer
        """
        layer, newId = self.createNewAttribute(layer, turn, 'id', lenght=255)
        layer, newHolder = self.createNewAttribute(layer, turn, 'holder', typer=self.holderAttributeType, lenght=self.holderAttributeLenght)
        if turn == 1:
            layer.startEditing()
            for feature in layer.getFeatures():
                layer.changeAttributeValue(feature.id(), self.getAttributesNames(layer).index(newHolder),
                                           str(feature.attribute(self.holderAttribute)))
            layer.commitChanges()
        else:
            layer.startEditing()
            for feature in layer.getFeatures():
                layer.changeAttributeValue(feature.id(), self.getAttributesNames(layer).index(newHolder),
                                           str(feature.attribute(self.actualHolderAttribute)))
            layer.commitChanges()
        self.actualIdAttribute = newId
        self.actualHolderAttribute = newHolder
        return layer

    def combine_with_constant_in_all(self, elements, constant=None):
        """
        DESCRIPTION: Create of a list with nested lists of all of the possible combinations
        INPUTS:
                elements: List of strings,
                constant: String, optional (need to be in every combination)
        OUTPUTS: List
        """
        all_combinations = []
        for r in range(1, len(elements) + 1):
            for combination in itertools.combinations(elements, r):
                if constant:
                    all_combinations.append(((constant,) + combination))
                else:
                    all_combinations.append(combination)
        # Remove the empty combination, as we want at least one element from `elements`
        all_combinations = [combo for combo in all_combinations if len(combo) <= 10]
        return all_combinations

    def checkTotalAreaThreshold(self, totalArea, holder):
        """
        DESCRIPTION: Check if a total area is inside the threshold or not 
        INPUTS:
                totalArea: Numeric
                holder: String, holder id
        OUTPUTS: Boolean
        """
        minimalBound = self.holdersTotalArea[holder] - (self.holdersTotalArea[holder] * (self.tolerance / 100))
        maximalBound = self.holdersTotalArea[holder] + (self.holdersTotalArea[holder] * (self.tolerance / 100))
        if maximalBound >= totalArea >= minimalBound:
            return True
        else:
            return False

    def calculateCombinationArea(self, combinations):
        """
        DESCRIPTION: Calculate a list of holding's total area 
        INPUTS:
                combinations: List, holding ids
        OUTPUTS: Numeric
        """
        temporaryArea = 0
        for combination in combinations:
            temporaryArea += self.holdingsWithArea[combination]
        return temporaryArea

    def isCloser(self, thresholdDistance, featureIds, seed, holder):
        """
        DESCRIPTION: Check if certain features are closer to the seed than the threshold
        INPUTS:
                thresholdDistance: Numeric
                featureIds: List, holding ids
                seed: String, holding id
        OUTPUTS: Boolean
        """
        isCloserBool = True
        sumDistance = 0
        for featureId in featureIds:
            distance = self.distanceMatrix[seed][featureId]
            #if distance > thresholdDistance or distance > self.holdingWithSeedDistance[featureId]:
            if distance > thresholdDistance:
                isCloserBool = False
            sumDistance += distance
        if sumDistance > self.totalDistances[holder]:
            isCloserBool = False
        return isCloserBool

    def maxDistance(self, featureIds, seed, layer=None):
        """
        DESCRIPTION: Calculate maximum distance of a list of holding and the seed polygon
        INPUTS:
                featureIds: List, holding ids
                seed: String, holding id
                layer: QgsVectorLayer, optional
        OUTPUTS: Numeric
        """
        maxDistance = 0
        for featureId in featureIds:
            try:
                distance = self.distanceMatrix[seed][featureId]
                if distance > maxDistance:
                    maxDistance = distance
            except KeyError:
                expression = f'"{self.idAttribute}" = \'{seed}\''
                layer.selectByExpression(expression)
                featureSeed = layer.selectedFeatures()[0]
                expression = f'"{self.idAttribute}" = \'{featureId}\''
                layer.selectByExpression(expression)
                featureTarget = layer.selectedFeatures()[0]
                # Get geometries of the features
                geometrySeed = featureSeed.geometry()
                geometryTarget = featureTarget.geometry()
                # Calculate the distance
                distance = geometrySeed.distance(geometryTarget)
                self.distanceMatrix[seed][featureId] = distance
                if distance > maxDistance:
                    maxDistance = distance
        return maxDistance

    def avgDistance(self, featureIds, seed, layer=None):
        """
        DESCRIPTION: Calculate average distance of a list of holding and the seed polygon
        INPUTS:
                featureIds: List, holding ids
                seed: String, holding id
                layer: QgsVectorLayer, optional
        OUTPUTS: Numeric
        """
        sumDistance = 0
        divider = 0
        for featureId in featureIds:
            divider += 1
            try:
                distance = self.distanceMatrix[seed][featureId]
                sumDistance += distance
            except KeyError:
                expression = f'"{self.idAttribute}" = \'{seed}\''
                layer.selectByExpression(expression)
                featureSeed = layer.selectedFeatures()[0]
                expression = f'"{self.idAttribute}" = \'{featureId}\''
                layer.selectByExpression(expression)
                featureTarget = layer.selectedFeatures()[0]
                # Get geometries of the features
                geometrySeed = featureSeed.geometry()
                geometryTarget = featureTarget.geometry()
                # Calculate the distance
                distance = geometrySeed.distance(geometryTarget)
                self.distanceMatrix[seed][featureId] = distance
                sumDistance += distance
        return sumDistance/divider

    def calculateCompositeNumber(self, seed, featureId):
        """
        DESCRIPTION: Calculate composite number of the wieght and the distance
        INPUTS:
                seed: String, holding id
                featureId: String, holding id
        OUTPUTS: Numeric
        """
        area = self.holdingsWithArea[featureId]
        distance = self.distanceMatrix[seed][featureId]
        return area*distance

    def setAttributeValues(self, layer, holder, targetHolder, tempHolderCombination, tempTargetCombination):
        """
        DESCRIPTION: Set attributes value of a certain swap
        INPUTS:
                layer: QgsVectorLayer
                holder: String, holder id
                targetHolder: String, holder id
                tempHolderCombination: List, holding ids
                tempTargetCombination: List, holding ids
        OUTPUTS: None
        """
        if len(tempHolderCombination) > 1 and len(tempTargetCombination) > 1:
            #many to many change
            for hold in tempHolderCombination:
                self.setNewAttribute(layer, hold, ','.join(tempHolderCombination), self.actualIdAttribute)
                self.setNewAttribute(layer, hold, targetHolder, self.actualHolderAttribute)
                self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(hold))
                self.holdersWithHoldings[targetHolder].append(hold)
            for ch in tempTargetCombination:
                self.setNewAttribute(layer, ch, ','.join(tempTargetCombination), self.actualIdAttribute)
                self.setNewAttribute(layer, ch, holder, self.actualHolderAttribute)
                self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(ch))
                self.holdersWithHoldings[holder].append(ch)
            self.counter += 1
        elif len(tempHolderCombination) > 1 and len(tempTargetCombination) == 1 or len(tempHolderCombination) == 1 and len(tempTargetCombination) > 1:
            #many to one change
            if len(tempHolderCombination) > 1:
                for hold in tempHolderCombination:
                    self.setNewAttribute(layer, hold, tempTargetCombination[0], self.actualIdAttribute)
                    self.setNewAttribute(layer, hold, targetHolder, self.actualHolderAttribute)
                    self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(hold))
                    self.holdersWithHoldings[targetHolder].append(hold)
                self.setNewAttribute(layer, tempTargetCombination[0], holder, self.actualHolderAttribute)
                self.setNewAttribute(layer, tempTargetCombination[0], ','.join(tempHolderCombination), self.actualIdAttribute)
                self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(tempTargetCombination[0]))
                self.holdersWithHoldings[holder].append(tempTargetCombination[0])
                self.counter += 1
            else:
                for ch in tempTargetCombination:
                    self.setNewAttribute(layer, ch, tempHolderCombination[0], self.actualIdAttribute)
                    self.setNewAttribute(layer, ch, holder, self.actualHolderAttribute)
                    self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(ch))
                    self.holdersWithHoldings[holder].append(ch)
                self.setNewAttribute(layer, tempHolderCombination[0], targetHolder, self.actualHolderAttribute)
                self.setNewAttribute(layer, tempHolderCombination[0], ','.join(tempTargetCombination), self.actualIdAttribute)
                self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(tempHolderCombination[0]))
                self.holdersWithHoldings[targetHolder].append(tempHolderCombination[0])
                self.counter += 1
        else:
            #one to one change
            self.setNewAttribute(layer, tempHolderCombination[0], tempTargetCombination[0], self.actualIdAttribute)
            self.setNewAttribute(layer, tempHolderCombination[0], targetHolder, self.actualHolderAttribute)
            self.setNewAttribute(layer, tempTargetCombination[0], tempHolderCombination[0], self.actualIdAttribute)
            self.setNewAttribute(layer, tempTargetCombination[0], holder, self.actualHolderAttribute)
            self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(tempTargetCombination[0]))
            self.holdersWithHoldings[holder].append(tempTargetCombination[0])
            self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(tempHolderCombination[0]))
            self.holdersWithHoldings[targetHolder].append(tempHolderCombination[0])
            self.counter += 1    

    def filterTouchinFeatures(self, layer, toSeed=False):
        """
        DESCRIPTION: Determine, if a holder holding touches its seed polygon. If true, filter it, from the changables, and can be mark as seed
        INPUTS:
                layer: QgsVectorLayer
                toSeed: Boolean
        OUTPUTS: None
        """
        algParams =  {
        'INPUT':layer,
        'FIELD':[self.actualHolderAttribute],
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        dissolvedLayer = processing.run("native:dissolve", algParams)['OUTPUT']

        algParams = {
        'INPUT':dissolvedLayer,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        simpliedLayer = processing.run("native:multiparttosingleparts", algParams)['OUTPUT']

        for turn, seed in enumerate(list(self.seeds.values())):
            if turn == 0:
                expression = f'"{self.idAttribute}" = \'{seed}\''
            else:
                expression += f'OR "{self.idAttribute}" = \'{seed}\''
        layer.selectByExpression(expression)

        algParams = {
            'INPUT': simpliedLayer,
            'PREDICATE':[1],
            'METHOD' : 0,
            'INTERSECT':QgsProcessingFeatureSourceDefinition(layer.source(), selectedFeaturesOnly=True, featureLimit=-1, geometryCheck=QgsFeatureRequest.GeometryAbortOnInvalid),
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        mergedSeed = processing.run("native:extractbylocation", algParams)['OUTPUT']

        algParams = {
            'INPUT': layer,
            'PREDICATE':[6],
            'METHOD' : 0,
            'INTERSECT':mergedSeed,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        processing.run("native:selectbylocation", algParams)
        selectedFeatures = layer.selectedFeatures()
        for feature in selectedFeatures:
            idValue = feature.attribute(self.idAttribute)
            self.globalChangables.pop(self.globalChangables.index(idValue))
            if toSeed:
                holderValue = feature.attribute(self.actualHolderAttribute)
                self.seeds[holderValue].append(idValue)

    def createMergedFile(self, layer, directory):
        """
        DESCRIPTION: Create merged file based on holders attribute field
        INPUTS:
                layer: QgsVectorLayer
                directory: String, absolute path to save the new layer
        OUTPUTS: None
        """
        lastHolderAttribute = int(self.actualHolderAttribute.split('_')[0])
        if lastHolderAttribute == self.steps-2:
            if self.steps-2 >= 10:
                attributeName = str(lastHolderAttribute) + self.actualHolderAttribute[2:]
            else:
                attributeName = str(lastHolderAttribute) + self.actualHolderAttribute[1:]
        else:
            if lastHolderAttribute >= 10:
                attributeName = str(lastHolderAttribute-1) + self.actualHolderAttribute[2:]
            else:
                attributeName = str(lastHolderAttribute-1) + self.actualHolderAttribute[1:]

        algParams = {
            'EXPRESSION': f'array_contains (overlay_touches (@layer, \"{attributeName}\", limit:=-1), \"{attributeName}\")',
            'INPUT': layer,
            'METHOD': 0
        }
        processing.run('qgis:selectbyexpression', algParams)

        # Extract selected features
        algParams = {
            'INPUT': layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selectedFeatures = processing.run('native:saveselectedfeatures', algParams)['OUTPUT']

        # Dissolve
        algParams = {
            'FIELD': [attributeName],
            'INPUT': selectedFeatures,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        dissolvedLayer = processing.run('native:dissolve', algParams)['OUTPUT']

        # Difference
        algParams = {
            'INPUT': layer,
            'OVERLAY': dissolvedLayer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        differences = processing.run('native:difference', algParams)['OUTPUT']

        # Merge vector layers
        algParams = {
            'CRS': layer.crs(),
            'LAYERS': [differences, dissolvedLayer],
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        mergedLayer = processing.run('native:mergevectorlayers', algParams)['OUTPUT']
        mergedLayer.setName(f'{os.path.basename(layer.source())[:-4]}')
        index = self.getAttributesNames(mergedLayer).index(self.weight)
        mergedLayer.startEditing()
        for feature in mergedLayer.getFeatures():
            holder = feature.attribute(attributeName)
            #newValue = self.holdersTotalArea[holder]
            newValue = feature.geometry().area()/10000
            mergedLayer.changeAttributeValue(feature.id(), index, newValue)
        mergedLayer.commitChanges()
        finalLayer = self.createTempLayer(mergedLayer, directory, "merged")
        layer.removeSelection()
        return finalLayer

    def calculateStatData(self, layer, fieldName):
        """
        DESCRIPTION: Calculate statistics infos about a layer and fields
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: Dictionary - Holder - # of holdings - Area - Average distance
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        statData = {}
        holdersWithHoldings, holdersHoldingNumber = self.getHoldersHoldings(layer, fieldName, self.idAttribute)
        for holder, holdings in holdersWithHoldings.items():
            data = {}
            totalArea = 0
            for holding in holdings:
                if self.holdingsWithArea[holding] != qgis.core.NULL:
                    totalArea += self.holdingsWithArea[holding]
            try :
                averageDistance = self.avgDistance(holdings, self.seeds[holder][0], layer)  
            except IndexError:
                averageDistance = 0
            except KeyError:
                averageDistance = 0
            data['ParcelNumber'] = holdersHoldingNumber[holder]
            data['TotalArea'] = totalArea
            data['AverageDistance'] = averageDistance
            statData[holder] = data

        return statData

    def createIndicesStat(self, beforeData, afterData, mergedData):
        """
        DESCRIPTION: Create statistics about the plugin run. Three statistics generate
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: QgsVectorLayer
        """
        indicators = QgsVectorLayer("NoGeometry?", "Indicators", "memory")
        indicators_data = indicators.dataProvider()
        indicators_data.addAttributes([QgsField('Number', QVariant.Int)])
        if 6 > self.holderAttributeType >= 2:
            indicators_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        elif self.holderAttributeType == 6:
            indicators_data.addAttributes([QgsField('Holder ID', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            indicators_data.addAttributes([QgsField('Holder ID', QVariant.String, len=self.holderAttributeLenght)])
        else:
            indicators_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        indicators_data.addAttributes([QgsField('BE # of parcels', QVariant.Int)])
        indicators_data.addAttributes([QgsField('BE Area (ha)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('BE Distance (m)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('AE # of parcels', QVariant.Int)])
        indicators_data.addAttributes([QgsField('AE Area (ha)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('AE Distance (m)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('Dif # of parcels', QVariant.Int)])
        indicators_data.addAttributes([QgsField('Dif Area (ha)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('Dif Distance (m)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('HFI (%)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('PFI (%)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('HDI (%)', QVariant.Double, "float", 10, 3)])
        indicators_data.addAttributes([QgsField('Change num', QVariant.Int)])
        indicators.updateFields()

        fields = indicators.fields()
        feats = []
        holders = list(self.holdersWithHoldings.keys())
        for turn, holder in enumerate(holders):
            feature = QgsFeature()
            # inform the feature of its fields
            feature.setFields(fields)
            feature['Number'] = turn
            feature['Holder ID'] = holder
            feature['BE # of parcels'] = beforeData[holder]['ParcelNumber']
            feature['BE Area (ha)'] = beforeData[holder]['TotalArea']
            feature['BE Distance (m)'] = beforeData[holder]['AverageDistance']
            feature['AE # of parcels'] = mergedData[holder]['ParcelNumber']
            feature['AE Area (ha)'] = afterData[holder]['TotalArea']
            feature['AE Distance (m)'] = afterData[holder]['AverageDistance']
            feature['Dif # of parcels'] = afterData[holder]['ParcelNumber'] - mergedData[holder]['ParcelNumber']
            feature['Dif Area (ha)'] = afterData[holder]['TotalArea'] - beforeData[holder]['TotalArea']
            feature['Dif Distance (m)'] = afterData[holder]['AverageDistance'] - beforeData[holder]['AverageDistance']
            feature['HFI (%)'] = (1 - (mergedData[holder]['ParcelNumber'] / beforeData[holder]['ParcelNumber']))*100
            feature['PFI (%)'] = ((afterData[holder]['TotalArea'] / beforeData[holder]['TotalArea']) - 1) *100
            try:
                feature['HDI (%)'] = (1 - (afterData[holder]['AverageDistance'] / beforeData[holder]['AverageDistance'])) *100
            except ZeroDivisionError:
                feature['HDI (%)'] = 0
            change_num = 0
            for holderAgain in holders:
                change_num += self.interactionTable[holder][holderAgain]
            feature['Change num'] = change_num
            feats.append(feature)

        indicators_data.addFeatures(feats)
        indicators.commitChanges()
        QgsProject.instance().addMapLayer(indicators)
        root = QgsProject().instance().layerTreeRoot()
        root.insertLayer(0, indicators)

        settingPath = os.path.join(QgsApplication.qgisSettingsDirPath(), 'styles', 'indicator_style.qml')
        if os.path.isfile(settingPath):
            indicators.loadNamedStyle(settingPath)
            indicators.triggerRepaint()

    def createExchangeLog(self, layer, actualHoldingId):
        beforeHoldersWithHoldings, beforeholdersHoldingNumber = self.getHoldersHoldings(layer, self.holderAttribute, self.idAttribute)
        afterHoldersWithHoldings, afterholdersHoldingNumber = self.getHoldersHoldings(layer, actualHoldingId, self.idAttribute)
        log = QgsVectorLayer("NoGeometry?", "Change log", "memory") 

        log_data = log.dataProvider()     
        log_data.addAttributes([QgsField('Number', QVariant.Int)])
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('Holder ID', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        log_data.addAttributes([QgsField('Get from parcel ID', QVariant.String)]) 
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('Get from land holder ID', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('Get from land holder ID', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('Get from land holder ID', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('Get from land holder ID', QVariant.Int)])
        log_data.addAttributes([QgsField('Transfer to parcel ID', QVariant.String)]) 
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('Transfer to land holder ID', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('Transfer to land holder ID', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('Transfer to land holder ID', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('Transfer to land holder ID', QVariant.Int)])     
        log_data.addAttributes([QgsField('Not changed parcel ID', QVariant.String)])
        log.updateFields()

        fields = log.fields()
        feats = []
        counter = 0
        for turn, holder in enumerate(list(beforeHoldersWithHoldings.keys())):
            beforeHoldings = beforeHoldersWithHoldings[holder]
            afterHoldings = afterHoldersWithHoldings[holder]

            notChanged = [hold for hold in beforeHoldings if hold in afterHoldings]
            received = [hold for hold in afterHoldings if hold not in beforeHoldings]
            donated = [hold for hold in beforeHoldings if hold not in afterHoldings]

            longestList = len(max([received, donated, notChanged], key=len))

            for turned in range(longestList):
                feature = QgsFeature()
                feature.setFields(fields)
                feature['Number'] = counter
                feature['Holder ID'] = holder
                if turned <= len(received)-1:
                    feature['Get from parcel ID'] = received[turned]
                    for key, value in beforeHoldersWithHoldings.items():
                        if received[turned] in value:
                            feature['Get from land holder ID'] = key
                            break
                else:
                    feature['Get from parcel ID'] =  qgis.core.NULL
                    feature['Get from land holder ID'] = qgis.core.NULL
                if turned <= len(donated)-1:
                    feature['Transfer to parcel ID'] = donated[turned]
                    for key, value in afterHoldersWithHoldings.items():
                        if donated[turned] in value:
                            feature['Transfer to land holder ID'] = key
                            break
                else:
                    feature['Transfer to parcel ID'] = qgis.core.NULL
                    feature['Transfer to land holder ID'] = qgis.core.NULL

                if turned <= len(notChanged)-1:
                    feature['Not changed parcel ID'] = notChanged[turned]
                feats.append(feature)
                counter += 1

            beforeHoldings = None
            afterHoldings = None

        log_data.addFeatures(feats)
        log.commitChanges()
        QgsProject.instance().addMapLayer(log)
        root = QgsProject().instance().layerTreeRoot()
        root.insertLayer(0, log)

    def createInteractionOutput(self):
        interactionTable = {}
        holders = list(self.holdersWithHoldings.keys()) 
        for holder in holders:
            interactionTable[holder] = {}
            for holderAgain in holders:
                interactionTable[holder][holderAgain] = 0

        self.interactionTable = interactionTable

    def saveInteractionOutput(self):
        holders = list(self.interactionTable.keys())
        holders.sort()
        
        log = QgsVectorLayer("NoGeometry?", "Swap frequency", "memory") 

        log_data = log.dataProvider()
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('from', QVariant.Int)])
            log_data.addAttributes([QgsField('to', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('from', QVariant.Double, "float", 10, 3)])
            log_data.addAttributes([QgsField('to', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('from', QVariant.String, len=self.holderAttributeLenght)])
            log_data.addAttributes([QgsField('to', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('from', QVariant.Int)])
            log_data.addAttributes([QgsField('to', QVariant.Int)])
        log_data.addAttributes([QgsField('weight', QVariant.Int)]) 
        
        log.updateFields()

        fields = log.fields()
        counter = 0

        fromList =[]
        toList = []
        weightList = []
        for holder in holders:
            for holderAgain in holders:
                interactionNum = self.interactionTable[holder][holderAgain]
                if interactionNum != 0:
                    if self.holderAttributeType == 10:
                        fromAttribute = holder
                        toAttribute = holderAgain
                    else:
                        if int(holder) > int(holderAgain):
                            fromAttribute = holder
                            toAttribute = holderAgain
                        elif int(holder) < int(holderAgain):
                            fromAttribute = holderAgain
                            toAttribute = holder
                        else:
                            fromAttribute = None
                            toAttribute = None
                    if fromAttribute and toAttribute:
                        if fromAttribute in fromList:
                            if toAttribute in toList:
                                indices = [i for i, val in enumerate(fromList) if val == fromAttribute]
                                for indice in indices:
                                    if toList[indice] == toAttribute:
                                        weightList[indice] += interactionNum
                        else:
                            fromList.append(fromAttribute)
                            toList.append(toAttribute)
                            weightList.append(interactionNum)

        zipped = list(zip(fromList, toList, weightList))
        zipped.sort()
        if len(zipped) > 0:
            fromList, toList, weightList = map(list, zip(*zipped))

            for x in range(len(fromList)):
                feature = QgsFeature()
                feature.setFields(fields)
                feature['from'] = fromList[x]
                feature['to'] = toList[x]
                feature['weight'] = weightList[x]
                log_data.addFeature(feature)
                log.commitChanges()

        log.commitChanges()
        QgsProject.instance().addMapLayer(log)
        root = QgsProject().instance().layerTreeRoot()
        root.insertLayer(0, log)  

        """
        with open(path,'w') as file:
            counter = 1
            file.write(f'ID;Old_Holder;New_Holder;Frequency\n')
            for holder in holders:
                for holderAgain in holders:
                    interactionNum = self.interactionTable[holder][holderAgain]
                    if interactionNum != 0:
                        file.write(f'{counter};{holder};{holderAgain};{interactionNum}\n')
                        counter += 1
        """

    def saveInteractionOutput2(self, layer, actualHoldingId):
        log = QgsVectorLayer("NoGeometry?", "Exchange frequency", "memory") 

        log_data = log.dataProvider()
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('from', QVariant.Int)])
            log_data.addAttributes([QgsField('to', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('from', QVariant.Double, "float", 10, 3)])
            log_data.addAttributes([QgsField('to', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('from', QVariant.String, len=self.holderAttributeLenght)])
            log_data.addAttributes([QgsField('to', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('from', QVariant.Int)])
            log_data.addAttributes([QgsField('to', QVariant.Int)])
        log_data.addAttributes([QgsField('weight', QVariant.Int)]) 
        
        log.updateFields()

        fields = log.fields()
        counter = 0

        beforeHoldersWithHoldings, beforeholdersHoldingNumber = self.getHoldersHoldings(layer, self.holderAttribute, self.idAttribute)
        afterHoldersWithHoldings, afterholdersHoldingNumber = self.getHoldersHoldings(layer, actualHoldingId, self.idAttribute)
        holders = list(self.holdersWithHoldings.keys())
        holders.sort()

        interactionTable = {}     
        for holder in holders:
            interactionTable[holder] = {}
            for holderAgain in holders:
                    exchangeNum = 0
                    fromHolderHoldings = beforeHoldersWithHoldings[holder]
                    toHolderHoldings = afterHoldersWithHoldings[holderAgain]
                    for holding in fromHolderHoldings:
                        if holding in toHolderHoldings:
                            exchangeNum += 1
                    interactionTable[holder][holderAgain] = exchangeNum
                    """
                    if not holderAgain in list(interactionTable.keys()):
                        try:
                            if not holder in list(interactionTable[holderAgain].keys()):
                                interactionTable[holder][holderAgain] = exchangeNum
                        except KeyError:
                            interactionTable[holder][holderAgain] = exchangeNum
                    """

        fromList =[]
        toList = []
        weightList = []
        for holder in holders:
            for holderAgain in holders:
                interactionNum = interactionTable[holder][holderAgain]
                if interactionNum != 0:
                    if self.holderAttributeType == 10:
                        fromAttribute = holder
                        toAttribute = holderAgain
                    else:
                        if int(holder) > int(holderAgain):
                            fromAttribute = holder
                            toAttribute = holderAgain
                        elif int(holder) < int(holderAgain):
                            fromAttribute = holderAgain
                            toAttribute = holder
                        else:
                            fromAttribute = None
                            toAttribute = None
                    if fromAttribute and toAttribute:
                        if fromAttribute in fromList:
                            if toAttribute in toList:
                                indices = [i for i, val in enumerate(fromList) if val == fromAttribute]
                                for indice in indices:
                                    if toList[indice] == toAttribute:
                                        weightList[indice] += interactionNum
                        else:
                            fromList.append(fromAttribute)
                            toList.append(toAttribute)
                            weightList.append(interactionNum)

        zipped = list(zip(fromList, toList, weightList))
        zipped.sort()
        fromList, toList, weightList = map(list, zip(*zipped))

        for x in range(len(fromList)):
            feature = QgsFeature()
            feature.setFields(fields)
            feature['from'] = fromList[x]
            feature['to'] = toList[x]
            feature['weight'] = weightList[x]
            log_data.addFeature(feature)
            log.commitChanges()

        QgsProject.instance().addMapLayer(log)
        root = QgsProject().instance().layerTreeRoot()
        root.insertLayer(0, log)  
    
    def saveInteractionOutputGOPA(self, path, layer, actualHoldingId):
        #import ptvsd
        #ptvsd.debug_this_thread()
        #Wrong method, but they use this
        beforeHoldersWithHoldings, beforeholdersHoldingNumber = self.getHoldersHoldings(layer, self.holderAttribute, self.idAttribute)
        afterHoldersWithHoldings, afterholdersHoldingNumber = self.getHoldersHoldings(layer, actualHoldingId, self.idAttribute)
        holders = list(self.holdersWithHoldings.keys())
        holders.sort()
        holdersReverse = holders.copy()
        holdersReverse.sort(reverse=True)
        oldIDList = []
        for holder in holders:
            holdings = beforeHoldersWithHoldings[holder]
            for hold in holdings:
                oldIDList.append(holder)
        newIDList = []
        for holder in holdersReverse:
            holdings = beforeHoldersWithHoldings[holder]
            for hold in holdings:
                newIDList.append(holder)

        with open(path,'w') as file:
            file.write(f'Old ID;New ID;Frequency\n')
            for holder in holders:
                index = oldIDList.index(holder)
                oldHolder = oldIDList[index]
                newHolder = newIDList[index]

                i = index
                start = i
                while i + 1 < len(oldIDList) and oldIDList[i] == oldIDList[i + 1] and newHolder == newIDList[i + 1]:
                    i += 1
                end = i
                count = end-start+1
                for x in range(count+1, 0, -1):
                    if x != 2:
                        file.write(f'{oldHolder};{newHolder};{x}\n')
                
    def calculateShapeIndexes(self, swapedLayer, mergedLayer):

        log = QgsVectorLayer("NoGeometry?", "Shape indexes", "memory") 

        log_data = log.dataProvider()
        if 6 > self.holderAttributeType >= 2:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        elif self.holderAttributeType == 6:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Double, "float", 10, 3)])
        elif self.holderAttributeType == 10:
            log_data.addAttributes([QgsField('Holder ID', QVariant.String, len=self.holderAttributeLenght)])
        else:
            log_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        log_data.addAttributes([QgsField('Holder ID', QVariant.Int)])
        log_data.addAttributes([QgsField('Parcel ID', QVariant.String)]) 
        log_data.addAttributes([QgsField('Length of sides - before', QVariant.Int)])
        log_data.addAttributes([QgsField('Acute angles - before', QVariant.Int)])
        log_data.addAttributes([QgsField('Reflex angles - before', QVariant.Int)])
        log_data.addAttributes([QgsField('Boundary points - before', QVariant.Int)])
        log_data.addAttributes([QgsField('Length of sides - after', QVariant.Int)])
        log_data.addAttributes([QgsField('Acute angles - after', QVariant.Int)])
        log_data.addAttributes([QgsField('Reflex angles - after', QVariant.Int)])
        log_data.addAttributes([QgsField('Boundary points - after', QVariant.Int)])
        
        log.updateFields()

        fields = log.fields()
        feats = []
        counter = 0

        for holder, seeds in self.seeds.items():
            if len(seeds) > 0:
                seed = seeds[0]
                feature = QgsFeature()
                feature.setFields(fields)
                feature['Number'] = counter
                feature['Holder ID'] = holder
                feature['Parcel ID'] = seed

                expression = f'"{self.idAttribute}" = \'{seed}\''
                swapedLayer.selectByExpression(expression)
                featureSeed = swapedLayer.selectedFeatures()[0]
                geometry = featureSeed.geometry()

                sideLengths = []
                angles = []
                regularity = None
                if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
                    # Handle single Polygon
                    sideLengths.extend(self.calculatePolygonSides(geometry.asPolygon()[0]))
                    angles.extend(self.calculateAngles(geometry.asPolygon()[0]))
                elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
                    # Handle MultiPolygon (a collection of polygons)
                    for polygon in geometry.asMultiPolygon():
                        sideLengths.extend(self.calculatePolygonSides(polygon))
                        angles.extend(self.calculateAngles(polygon))

                feature['Length of sides - before'] = sum(sideLengths)
                feature['Acute angles - before'] = len([angle for angle in angles if angle < 90])
                feature['Reflex angles - before'] = len([angle for angle in angles if angle > 180 and angle < 360])
                feature['Boundary points - before'] = len(angles)

                algParams = {
                    'INPUT': swapedLayer,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                }
                selectedFeatures = processing.run("native:saveselectedfeatures",algParams)["OUTPUT"]

                algParams = {
                    'INPUT': mergedLayer,
                    'PREDICATE':[1,5],
                    'INTERSECT':selectedFeatures,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                }
                processing.run("native:selectbylocation", algParams)
                selectedFeatures = mergedLayer.selectedFeatures()[0]
                geometry = selectedFeatures.geometry()

                sideLengths = []
                angles = []
                regularity = None
                if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
                    # Handle single Polygon
                    sideLengths.extend(self.calculatePolygonSides(geometry.asPolygon()[0]))
                    angles.extend(self.calculateAngles(geometry.asPolygon()[0]))
                elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
                    # Handle MultiPolygon (a collection of polygons)
                    for polygon in geometry.asMultiPolygon():
                        sideLengths.extend(self.calculatePolygonSides(polygon))
                        angles.extend(self.calculateAngles(polygon))

                feature['Length of sides - after'] = sum(sideLengths)
                feature['Acute angles - after'] = len([angle for angle in angles if angle < 90])
                feature['Reflex angles - after'] = len([angle for angle in angles if angle > 180 and angle < 360])
                feature['Boundary points - after'] = len(angles)     
                feats.append(feature)
                counter += 1

        swapedLayer.removeSelection()
        mergedLayer.removeSelection()

        log_data.addFeatures(feats)
        log.commitChanges()
        QgsProject.instance().addMapLayer(log)
        root = QgsProject().instance().layerTreeRoot()
        root.insertLayer(0, log)    

    def calculatePolygonSides(self, ring):
        sides = []
        # Loop through the vertices of the ring and calculate the distances
        if type(ring[0]) == list:
            ring = ring[0]
        for i in range(len(ring) - 1):
            startPoint = ring[i]
            endPoint = ring[i + 1]
            distance = startPoint.distance(endPoint)
            sides.append(distance)
        
        # To close the polygon, calculate distance between the last and first point
        distance = ring[-1].distance(ring[0])
        sides.append(distance)
        
        return sides

    def calculate_angle(self, v1, v2):
        # Dot product of vectors v1 and v2
        dot_product = v1.x() * v2.x() + v1.y() * v2.y()
        
        # Magnitudes (lengths) of the vectors
        mag_v1 = math.sqrt(v1.x()**2 + v1.y()**2)
        mag_v2 = math.sqrt(v2.x()**2 + v2.y()**2)
        
        # Calculate cosine of the angle using the dot product formula
        cos_angle = dot_product / (mag_v1 * mag_v2)
        
        # Get the angle in radians and convert to degrees
        cos_angle = max(-1, min(1, cos_angle))
        angle_rad = math.acos(cos_angle)
        angle_deg = math.degrees(angle_rad)
        
        return angle_deg

    # Function to calculate angles at each vertex of a polygon
    def calculateAngles(self, polygon):
        angles = []

        if type(polygon[0]) == list:
            polygon = polygon[0]
        
        # Loop through each vertex of the polygon
        for i in range(len(polygon)):
            # Get previous, current, and next points to form vectors
            if i == 0:
                prevPoint = polygon[i - 2]  # Previous point (circular)
                currentPoint = polygon[i]   # Current point
                nextPoint = polygon[i + 1]  # Next point (circular)
            elif i == len(polygon)-1:
                prevPoint = polygon[i - 1]  # Previous point (circular)
                currentPoint = polygon[i]   # Current point
                nextPoint = polygon[1]  # Next point (circular)
            else:
                prevPoint = polygon[i - 1]  # Previous point (circular)
                currentPoint = polygon[i]   # Current point
                nextPoint = polygon[i + 1]  # Next point (circular)
            
            # Create vectors: 
            # Vector from current to previous point
            v1 = QgsPoint(prevPoint.x() - currentPoint.x(), prevPoint.y() - currentPoint.y())
            # Vector from current to next point
            v2 = QgsPoint(nextPoint.x() - currentPoint.x(), nextPoint.y() - currentPoint.y())
            
            # Calculate the angle between the two vectors
            angle = self.calculate_angle(v1, v2)
            angles.append(angle)

        return angles

    def hybrid_method(self, layer, feedback):
        """
        DESCRIPTION: Function for make swap to get group holder's holdings neighbours or closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
        INPUTS:
                layer: QgsVectorLayer
                feedback: QgsProcessingMultiStepFeedback
                seeds: List, holding ids
        OUTPUTS: QgsVectorLayer
        """
        #import ptvsd
        #ptvsd.debug_this_thread()
        changes = 1
        changer = True
        self.globalChangables = self.getChangableHoldings()
        turn = 0
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)

        feedback.pushInfo('Hybrid algorithm started')

        while changer:
            turn += 1
            layer = self.setTurnAttributes(layer, turn)
            localChangables = copy.deepcopy(self.globalChangables)
            feedback.pushInfo(f'Turn {turn}')

            turnHolders = list(self.holdersWithHoldings.keys())
            turnHolders = [holder for holder in turnHolders if len(self.seeds[holder]) > 0 and holder != 'NULL']
            startHolder = random.choice(turnHolders)
            holder = startHolder
            measure = True

            while measure:
                holdings = self.holdersWithHoldings[holder]
                holderTotalArea = holdersLocalTotalArea[holder]
                seedList = self.seeds[holder]
                seed = seedList[0]

                # List of all holdings, with distance to the current seed
                inDistance = self.filteredDistanceMatrix[seed]
                # List of holdings, which are not seeds, with distance to the current seed
                distanceChanges = self.getChangableHoldings(inDistance)
                # List of holder's holdings, which are in distance
                filteredHolderHoldingsIds = self.idsForChange(holdings, distanceChanges)
                # Filter holder's holdings, which are suitable for change
                filteredHolderHoldingsIds = self.idsForChange(filteredHolderHoldingsIds, localChangables)
                # Security check, if the holdings still is the holder property (not changed during loop)
                filteredHolderHoldingsIds = self.idsForChange(filteredHolderHoldingsIds, self.holdersWithHoldings[holder])
                # Check if the holdings are closest to the certain seed
                #filteredHolderHoldingsIds = self.holdingsClosestToSeed(filteredHolderHoldingsIds, seed, seedList)

                sortedDistances = [(y, x) for y, x in zip(list(inDistance.values()), list(inDistance.keys())) if x in filteredHolderHoldingsIds]
                sortedDistances.sort()
                filteredHolderHoldingsIds = [key for value, key in sortedDistances[:5]]
                holderHoldingsCombinations = self.combine_with_constant_in_all(filteredHolderHoldingsIds)

                minAreaHolding = min([self.holdingsWithArea[hold] for hold in holdings])*((100-self.tolerance)/100)

                tempHolderCombination = None
                tempTargetCombination = None
                tempHolderTotalArea = None
                tempTargetTotalArea = None
                targetHolder = None
                measure = None

                filteredLocalChangables = []
                for distance in distanceChanges:
                    if distance not in filteredHolderHoldingsIds and distance in localChangables:
                        filteredLocalChangables.append(distance)

                neighboursIds, neighboursLayer = self.getNeighbours(layer, seed)
                neighboursHolders = list(set([neighboursFeature.attribute(self.actualHolderAttribute) for neighboursFeature in neighboursLayer.getFeatures()]))
                del neighboursIds, neighboursLayer, distance, distanceChanges

                targetHolders = []
                targetHolders.extend(neighboursHolders)
                for changable in filteredLocalChangables:
                    for allHolder, allHoldings in self.holdersWithHoldings.items():
                        if changable in allHoldings and allHolder not in targetHolders and allHolder != 'NULL' and holdersLocalTotalArea[allHolder] > minAreaHolding and allHolder not in neighboursHolders:
                            targetHolders.append(allHolder)
                del changable, allHolder, allHoldings, minAreaHolding

                for tempTargetHolder in targetHolders:
                    targetHolderSeed = self.seeds[tempTargetHolder]
                    if len(targetHolderSeed) > 0:
                        targetHolderSeed = self.seeds[tempTargetHolder][0]
                        filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables and hold != targetHolderSeed]
                        if len(filteredLocalChangables) > 0:
                            targetAllCombinations = self.combine_with_constant_in_all(filteredLocalTargetHoldings)
                            combTurn = 0
                            for targetCombination in targetAllCombinations:
                                if len(targetAllCombinations) == 1:
                                    targetCombination = targetAllCombinations[0]
                                if combTurn < 200000:
                                    combTurn += 1
                                else:
                                    break
                                targetMaxDistance = self.maxDistance(targetCombination, targetHolderSeed, layer)
                                targetAvgDistanceOld = self.avgDistance(targetCombination, targetHolderSeed, layer)
                                if len(holderHoldingsCombinations) > 1:
                                    for holderCombination in holderHoldingsCombinations:
                                        if len(holderHoldingsCombinations) == 1:
                                            holderCombination = holderHoldingsCombinations
                                        # Maximum distance check
                                        holderMaxDistance = self.maxDistance(holderCombination, seed, layer)
                                        targetCloser = self.isCloser(holderMaxDistance, targetCombination, seed, holder)
                                        holderCloser = self.isCloser(targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder)
                                        if targetCloser and holderCloser:
                                            #Average distance check
                                            holderAvgDistanceOld = self.avgDistance(holderCombination, seed, layer)
                                            holderAvgDistanceNew = self.avgDistance(targetCombination, seed, layer)
                                            targetAvgDistanceNew = self.avgDistance(holderCombination, targetHolderSeed, layer)
                                            if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                                                #Shape check
                                                if self.checkShape(layer, seed, holdings, holderCombination, targetCombination):
                                                    #Total area check
                                                    newHolderTotalArea = holderTotalArea - self.calculateCombinationArea(holderCombination) + self.calculateCombinationArea(targetCombination)
                                                    newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - self.calculateCombinationArea(targetCombination) + self.calculateCombinationArea(holderCombination)
                                                    if self.checkTotalAreaThreshold(newHolderTotalArea, holder) and self.checkTotalAreaThreshold(newTargetTotalArea, tempTargetHolder):
                                                        #Create composite number for ranking
                                                        localMeasure = sum([self.calculateCompositeNumber(seed, tempId) for tempId in holderCombination])
                                                        if not measure:
                                                            targetHolder = tempTargetHolder
                                                            tempHolderCombination = holderCombination
                                                            tempTargetCombination = targetCombination
                                                            measure = localMeasure
                                                            tempHolderTotalArea = newHolderTotalArea
                                                            tempTargetTotalArea = newTargetTotalArea
                                                        else:
                                                            if measure < localMeasure:
                                                                targetHolder = tempTargetHolder
                                                                tempHolderCombination = holderCombination
                                                                tempTargetCombination = targetCombination
                                                                measure = localMeasure
                                                                tempHolderTotalArea = newHolderTotalArea
                                                                tempTargetTotalArea = newTargetTotalArea                                  
                    else:
                        filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables]
                        
                        targetAllCombinations = self.combine_with_constant_in_all(filteredLocalTargetHoldings)
                        targetCombinations = []
                        originCombinations = []
                        combTurn = 0
                        for targetCombination in targetAllCombinations:
                            if combTurn < 200000:
                                combTurn += 1
                            else:
                                break
                            for holderCombination in holderHoldingsCombinations:
                                #Maximum distance check
                                holderMaxDistance = self.maxDistance(holderCombination, seed, layer)
                                holderCloser = self.isCloser(holderMaxDistance, sortedCombination, seed, holder)
                                if holderCloser:
                                    #Average distance check
                                    holderAvgDistanceOld = self.avgDistance(holderCombination, seed, layer)
                                    holderAvgDistanceNew = self.avgDistance(sortedCombination, seed, layer)
                                    if holderAvgDistanceNew < holderAvgDistanceOld:
                                        #Shape check
                                        holderSeedShape = self.checkShape(layer, seed, holdings, holderCombination, sortedCombination)
                                        if holderSeedShape:
                                            #Total area check
                                            newHolderTotalArea = holderTotalArea - self.calculateCombinationArea(holderCombination) + self.calculateCombinationArea(targetCombination)
                                            newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - self.calculateCombinationArea(targetCombination) + self.calculateCombinationArea(holderCombination)
                                            if self.checkTotalAreaThreshold(newHolderTotalArea, holder) and self.checkTotalAreaThreshold(newTargetTotalArea, tempTargetHolder):
                                                #Create composite number for ranking
                                                localMeasure = sum([self.calculateCompositeNumber(seed, tempId) for tempId in holderCombination])
                                                if not measure:
                                                    targetHolder = tempTargetHolder
                                                    tempHolderCombination = holderCombination
                                                    tempTargetCombination = targetCombination
                                                    measure = localMeasure
                                                    tempHolderTotalArea = newHolderTotalArea
                                                    tempTargetTotalArea = newTargetTotalArea
                                                else:
                                                    if measure < localMeasure:
                                                        targetHolder = tempTargetHolder
                                                        tempHolderCombination = holderCombination
                                                        tempTargetCombination = targetCombination
                                                        measure = localMeasure
                                                        tempHolderTotalArea = newHolderTotalArea
                                                        tempTargetTotalArea = newTargetTotalArea  
                    if measure:
                        break   
                if measure:
                    if len(self.seeds[targetHolder]) > 0:
                        targetHolderSeed = self.seeds[targetHolder][0]
                    else:
                        targetHolderSeed = False
                    self.setAttributeValues(layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                    if self.stats:
                        self.interactionTable[holder][targetHolder] += 1
                        self.interactionTable[targetHolder][holder] += 1
                    if len(tempHolderCombination) > 1 and len(tempTargetCombination) > 1:
                        #many to many change
                        for hold in tempHolderCombination:
                            localChangables.pop(localChangables.index(hold))
                            self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold]
                            self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                            self.totalDistances[targetHolder] += self.holdingWithSeedDistance[hold]
                        for ch in tempTargetCombination:
                            localChangables.pop(localChangables.index(ch))
                            self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch]
                            self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                            self.totalDistances[holder] += self.holdingWithSeedDistance[ch]
                        holdersLocalTotalArea[holder] = tempHolderTotalArea
                        holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                        commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination}'
                        logging.debug(commitMessage)
                        feedback.pushInfo(commitMessage)
                    elif (len(tempHolderCombination) > 1 and len(tempTargetCombination) == 1) or (len(tempHolderCombination) == 1 and len(tempTargetCombination) > 1):
                        #many to one change
                        if len(tempHolderCombination) > 1:
                            for hold in tempHolderCombination:
                                localChangables.pop(localChangables.index(hold))
                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold] 
                                self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                                self.totalDistances[targetHolder] += self.distanceMatrix[targetHolderSeed][hold]
                            localChangables.pop(localChangables.index(tempTargetCombination[0]))
                            self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]]
                            self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                            self.totalDistances[holder] += self.distanceMatrix[seed][tempTargetCombination[0]]
                            commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination[0]}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)
                        else:
                            for ch in tempTargetCombination:
                                localChangables.pop(localChangables.index(ch))
                                self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch] 
                                self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                                self.totalDistances[holder] += self.distanceMatrix[seed][ch]
                            localChangables.pop(localChangables.index(tempHolderCombination[0]))
                            self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]]
                            self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                            self.totalDistances[targetHolder] += self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                            commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)
                        holdersLocalTotalArea[holder] = tempHolderTotalArea
                        holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                    else:
                        #one to one change
                        localChangables.pop(localChangables.index(tempTargetCombination[0]))
                        localChangables.pop(localChangables.index(tempHolderCombination[0]))
                        self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]]
                        self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]]
                        self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                        self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                        self.totalDistances[holder] += self.holdingWithSeedDistance[tempTargetCombination[0]]
                        self.totalDistances[targetHolder] += self.holdingWithSeedDistance[tempHolderCombination[0]]
                        holdersLocalTotalArea[holder] = tempHolderTotalArea
                        holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                        commitMessage = f'Change {str(self.counter)} for {tempTargetCombination[0]} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination[0]}'
                        logging.debug(commitMessage)
                        feedback.pushInfo(commitMessage)
                    turnHolders.pop(turnHolders.index(holder))
                    if targetHolder in turnHolders:
                        holder = copy.copy(targetHolder)
                    else:
                        holder = random.choice(turnHolders)
                else:
                    if len(turnHolders) != 0:
                        turnHolders.pop(turnHolders.index(holder))
                        holder = random.choice(turnHolders)
                        measure = True
                    else:
                        holder = None
                        break
                if feedback.isCanceled():
                    self.endLogging()
                    return {}

            if feedback.isCanceled():
                self.endLogging() 
                return {}
            if turn == 1:
                logging.debug(f'Changes in turn {turn}: {self.counter}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                if self.counter == 0:
                    changer = False
                    return None
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filterTouchinFeatures(layer) 
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
                if changes == self.counter:
                    changer = False
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                    indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filterTouchinFeatures(layer)
            feedback.setCurrentStep(1+turn)
            feedback.pushInfo('Save turn results to the file')

        return layer

    def square_simmilarity(self, geometry):
        sideLengths = []
        angles = []

        if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
            # Handle single Polygon
            sideLengths.extend(self.calculatePolygonSides(geometry.asPolygon()[0]))
            angles.extend(self.calculateAngles(geometry.asPolygon()[0]))
        elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
            # Handle MultiPolygon (a collection of polygons)
            for polygon in geometry.asMultiPolygon():
                sideLengths.extend(self.calculatePolygonSides(polygon))
                angles.extend(self.calculateAngles(polygon))
        else:
            # Handle single Polygon
            sideLengths.extend(self.calculatePolygonSides(geometry.asPolygon()[0]))
            angles.extend(self.calculateAngles(geometry.asPolygon()[0]))
        averageSide = sum(sideLengths)/len(sideLengths)
        lenghtVariation = statistics.stdev(sideLengths)/averageSide

        angleVariation = statistics.stdev(angles) / 90

        similarity = max(0, 1 - (lenghtVariation + angleVariation) / 2)

        return similarity

    def checkShape(self, layer, seed, holdings, holderCombination, sortedCombination):
        #import ptvsd
        #ptvsd.debug_this_thread()
        algParams =  {
        'INPUT':layer,
        'FIELD':[self.actualHolderAttribute],
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        dissolvedLayer = processing.run("native:dissolve", algParams)['OUTPUT']

        algParams = {
        'INPUT':dissolvedLayer,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        simpliedLayer = processing.run("native:multiparttosingleparts", algParams)['OUTPUT']
        
        algParams = {
        'INPUT':simpliedLayer,
        'METHOD':0,
        'TOLERANCE':5,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        simpliedLayer = processing.run("native:simplifygeometries", algParams)['OUTPUT']

        expression = f'"{self.idAttribute}" = \'{seed}\''
        layer.selectByExpression(expression)
        seedFeature = layer.selectedFeatures()[0]

        algParams = {
                'INPUT': layer,
                'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selectedFeatures = processing.run('native:saveselectedfeatures', algParams)['OUTPUT']

        algParams = {
            'INPUT': simpliedLayer,
            'PREDICATE':[1, 3, 5],
            'METHOD' : 0,
            'INTERSECT':selectedFeatures,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        mergedSeed = processing.run("native:extractbylocation", algParams)['OUTPUT']

        mergedFeature = next(mergedSeed.getFeatures())
        mergedSeedGeometry = mergedFeature.geometry()

        seedGeometry = layer.selectedFeatures()[0].geometry()
        geometriesToMerge = [seedGeometry]
        layer.removeSelection()

        holdings.extend(holderCombination)

        for hold in holdings:
            if hold not in sortedCombination and hold != seed:
                expression = f'"{self.idAttribute}" = \'{hold}\''
                layer.selectByExpression(expression)
                holdGeometry = layer.selectedFeatures()[0].geometry()
                if holdGeometry.touches(seedGeometry) or holdGeometry.intersects(seedGeometry):
                    geometriesToMerge.append(holdGeometry)
                layer.removeSelection()
        
        newSeedGeometry = QgsGeometry.unaryUnion(geometriesToMerge)

        if mergedSeedGeometry == newSeedGeometry:
            return True
        else:
            originalShapeSimilarity = self.square_simmilarity(mergedSeedGeometry)
            newShapeSimilarity = self.square_simmilarity(newSeedGeometry)
            if newShapeSimilarity >= originalShapeSimilarity:
                return True
            else:
                return False

    def holdingsClosestToSeed(self, holdings, seed, seedList):
        closestHoldings = []
        for holding in holdings:
            closestSeed = None
            closestDistance = None
            for sed in seedList:
                if closestSeed and closestDistance:
                    try:
                        distance = self.distanceMatrix[sed][holding]
                    except KeyError:
                        expression = f'"{self.idAttribute}" = \'{sed}\''
                        layer.selectByExpression(expression)
                        featureSeed = layer.selectedFeatures()[0]
                        expression = f'"{self.idAttribute}" = \'{holding}\''
                        layer.selectByExpression(expression)
                        featureTarget = layer.selectedFeatures()[0]
                        # Get geometries of the features
                        geometrySeed = featureSeed.geometry()
                        geometryTarget = featureTarget.geometry()
                        # Calculate the distance
                        distance = geometrySeed.distance(geometryTarget)
                    if distance < closestDistance:
                        closestSeed = sed
                        closestDistance = distance
                else:
                    try:
                        distance = self.distanceMatrix[sed][holding]
                    except KeyError:
                        expression = f'"{self.idAttribute}" = \'{sed}\''
                        layer.selectByExpression(expression)
                        featureSeed = layer.selectedFeatures()[0]
                        expression = f'"{self.idAttribute}" = \'{holding}\''
                        layer.selectByExpression(expression)
                        featureTarget = layer.selectedFeatures()[0]
                        # Get geometries of the features
                        geometrySeed = featureSeed.geometry()
                        geometryTarget = featureTarget.geometry()
                        # Calculate the distance
                        distance = geometrySeed.distance(geometryTarget)
                    closestSeed = sed
                    closestDistance = distance
            if closestSeed == seed:
                closestHoldings.append(holding)
        return closestHoldings

    def copyStyle(self, templateLayer, targetLayer):
        tmp_qml = os.path.join(tempfile.gettempdir(), 'temp_style.qml')
        templateLayer.saveNamedStyle(tmp_qml)

        # Load style into target layer
        targetLayer.loadNamedStyle(tmp_qml)
        targetLayer.triggerRepaint()

        #try to reconfigure attr value
        renderer = targetLayer.renderer()
        if hasattr(renderer, 'setClassAttribute'):
            lastHolderAttribute = int(self.actualHolderAttribute.split('_')[0])
            if lastHolderAttribute == self.steps-2:
                if self.steps-2 >= 10:
                    holderAttribute = str(lastHolderAttribute) + self.actualHolderAttribute[2:]
                else:
                    holderAttribute = str(lastHolderAttribute) + self.actualHolderAttribute[1:]
            else:
                if lastHolderAttribute >= 10:
                    holderAttribute = str(lastHolderAttribute-1) + self.actualHolderAttribute[2:]
                else:
                    holderAttribute = str(lastHolderAttribute-1) + self.actualHolderAttribute[1:]
            renderer.setClassAttribute(holderAttribute)
            targetLayer.triggerRepaint()

        os.remove(tmp_qml)

    def cleanMergedLayer(self, toDeletAttr, layer):
        layer.startEditing()
        indexes = []
        lastHolderAttribute = int(self.actualHolderAttribute.split('_')[0])
        if lastHolderAttribute == self.steps-2:
            if self.steps-2 >= 10:
                holderAttribute = str(lastHolderAttribute) + self.actualHolderAttribute[2:]
            else:
                holderAttribute = str(lastHolderAttribute) + self.actualHolderAttribute[1:]
        else:
            if lastHolderAttribute >= 10:
                holderAttribute = str(lastHolderAttribute-1) + self.actualHolderAttribute[2:]
            else:
                holderAttribute = str(lastHolderAttribute-1) + self.actualHolderAttribute[1:]
        for attributeName in toDeletAttr:
            if attributeName not in [self.idAttribute, holderAttribute]:
                indexes.append(layer.fields().indexFromName(attributeName))
        layer.deleteAttributes(indexes)
        layer.updateFields()
        layer.commitChanges()