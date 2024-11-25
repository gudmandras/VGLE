__author__ = 'GOPA'
__date__ = '2024-09-05'
__copyright__ = '(C) 2024 by GOPA'
__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (Qgis,
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
                        QgsLayerTree, 
                        QgsFeature,
                        QgsField, 
                        QgsVectorFileWriter, 
                        QgsVectorLayer,
                        QgsFeatureRequest, 
                        QgsExpression,
                        QgsCoordinateReferenceSystem)
import processing
import qgis.core
import os.path
from datetime import datetime
import time, copy, uuid, logging, itertools, sys, random

class PolygonGrouper(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        #import ptvsd
        #ptvsd.debug_this_thread()
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features', defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features', defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False))
        self.addParameter(QgsProcessingParameterField('AssignedByField', 'Assigned by field', type=QgsProcessingParameterField.Any, parentLayerParameterName='Inputlayer', allowMultiple=True, defaultValue=''))
        self.addParameter(QgsProcessingParameterField('BalancedByField', 'Balanced by field', type=QgsProcessingParameterField.Numeric, parentLayerParameterName='Inputlayer', allowMultiple=False, defaultValue=''))
        self.addParameter(QgsProcessingParameterNumber('Tolerance', 'Tolerance (%)', type=QgsProcessingParameterNumber.Integer, minValue=5, maxValue=100, defaultValue=5))
        self.addParameter(QgsProcessingParameterNumber('DistanceTreshold', 'Distance treshold (m)', type=QgsProcessingParameterNumber.Integer, minValue=0, defaultValue=1000))
        self.addParameter(QgsProcessingParameterEnum('SwapToGet', 'Swap to get', options=['Neighbours','Closer','Neighbours, then closer','Closer, then neighbours'], allowMultiple=False, defaultValue='Neighbours'))
        self.addParameter(QgsProcessingParameterBoolean('Simplfy', "Simplfy algorithm to process big dataset", defaultValue=False))
        self.addParameter(QgsProcessingParameterFile('OutputDirectory', 'Output directory', behavior=QgsProcessingParameterFile.Folder, fileFilter='Minden fájl (*.*)', defaultValue=None))
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]
        self.counter = 0
        

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
        <p>Algorithm for spatialy grouping polygons which have the same value in certain field. The spatial grouping balanced by balanced by field parameter and threshold. Besides, the range of the grouping can be decrease with the distance threshold.</p>
        <h2>Input parameters</h2>
        <h3>Input layer</h3>
        <p>Vector layer with polygon geometries.</p>
        <h3>Give preference for the selected features</h3>
        <p>The selected features in the input layer will be used as seed polygons, and the grouping will be around these features. Without these, the seed polygons are the largest polygons per the assigned by field unique values.</p>
        <h3>Only use the selected features</h3>
        <p>Only works, if the "Give preference for the selected features" parameter is True. If checked, only the selected polygons will used, else the selected and the largest polygons together.</p>
        <h3>Use single holding's holders polygons</h3>
        <p>Use holder's polygons, which have only one polygon.</p>
        <h3>Assigned by field</h3>
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
        Closer, then neighbours: Combinated run, first Closer, than Neighbours function will be run on the results of the Closer.</p>
        <h3>Simplfy</h3>
        <p>Simplfy algorithm to process big dataset. Recommended, when the input dataset contains more than 4000 polygon. With the simplyfied algorithm, not all of the swap combination is investigated, but a sampled size only.</p>
        <h3>Output directory</h3>
        <p>The directory where the outputs will saved. Two output layer will be created, with timeStamp in their names:
        First: Vector layer, with the base layer name + algorithm name + timeStamp
        Second: Merged layer, a dissolved vector layer based on the final state of the grouping. (Because of the dissolving, only the last field is valid in this layer)</p>
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
            feedback.pushInfo(f"'Only use the selected features' parameters works only with 'Give preference for the selected features parameter'. This parameter is invalided") 
        
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
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
       
        self.startLogging(inputLayer, parameters, timeStamp)
        #Create work file and get the starting dictionaries
        tempLayer = self.createTempLayer(inputLayer, parameters["OutputDirectory"], self.algorithmNames[self.algorithmIndex].lower(), timeStamp)
        layer, self.holderAttribute = self.setHolderField(tempLayer, parameters["AssignedByField"])
        self.holderAttributeType, self.holderAttributeLenght = self.getFieldProperties(tempLayer, self.holderAttribute)
        holdersWithHoldings = self.getHoldersHoldings(layer)
        layer, self.idAttribute, holdersWithHoldings = self.createIdField(layer, holdersWithHoldings)
        layer.dataProvider().createSpatialIndex()
        holdingsWithArea = self.getHoldingsAreas(layer, parameters["BalancedByField"])
        self.holdersWithHoldings = holdersWithHoldings
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

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            self.endLogging()
            return {}
        #Start one of the functions
        if self.algorithmIndex == 0:
            swapedLayer = self.neighbours(layer, feedback)
        elif self.algorithmIndex == 1:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                swapedLayer = self.closer(layer, feedback)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 2:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                originalSeeds = copy.deepcopy(self.seeds)
                swapedLayer = self.neighbours(layer, feedback)
                swapedLayer = self.closer(swapedLayer, feedback, originalSeeds)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 3:
            oneSeedBoolean = self.checkSeedNumber(feedback)
            if oneSeedBoolean:
                swapedLayer = self.closer(layer, feedback)
                swapedLayer = self.neighbours(swapedLayer, feedback)
            else:
                swapedLayer = False
        #Save results and create merged file
        if swapedLayer:
            feedback.setCurrentStep(self.steps-1)

            swapedLayer.commitChanges()
            QgsProject.instance().addMapLayer(swapedLayer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, swapedLayer)

            mergedLayer = self.createMergedFile(swapedLayer, parameters["OutputDirectory"])

            QgsProject.instance().addMapLayer(mergedLayer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, mergedLayer)

            mainEndTime = time.time()
            logging.debug(f'Script time:{mainEndTime-mainStartTime}')

            feedback.setCurrentStep(self.steps)
            self.endLogging()   
            results['OUTPUT'] = swapedLayer
            return results
        else:
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

    def getHoldersHoldings(self, layer):
        """
        DESCRIPTION: Create a dictionary for holder and their holdings
        INPUTS:
                layer: QgsVectorLayer
        OUTPUTS: Dictionary, key: holders ids, values: List, holdings ids
        """
        holdersWithHoldings = {}
        features = layer.getFeatures()
        for feature in features:
            featureId = feature.id()
            holder = feature.attribute(self.holderAttribute)
            if holder != qgis.core.NULL:
                if holder in list(holdersWithHoldings.keys()):
                    holdersWithHoldings[holder].append(featureId)
                else:
                    holdersWithHoldings[holder] = [featureId]
        return holdersWithHoldings

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
        if preference:
            algParams = {
                'INPUT': layer,
                'PREDICATE':[3],
                'INTERSECT':selectedFeatures,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            processing.run("native:selectbylocation", algParams)['OUTPUT']
            selectedFeatures = layer.selectedFeatures()
            for feature in selectedFeatures:
                holderValue = feature.attribute(self.holderAttribute)
                idValue = feature.attribute(self.idAttribute)
                if holderValue not in list(holdersWithSeeds.keys()):
                    holdersWithSeeds[holderValue] = [idValue]
                else:
                    holdersWithSeeds[holderValue].append(idValue)
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

    def neighbours(self, layer, feedback):
        """
        DESCRIPTION: Function for make swap to get group holder's holdings around their seed polygons. The function try to swap the neighbour polygons for other holdings of the holder. 
        INPUTS:
                layer: QgsVectorLayer
                feedback: QgsProcessingMultiStepFeedback
        OUTPUTS: QgsVectorLayer
        """
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
        except AttributeError:
            turn = 0

        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
        feedback.pushInfo('Neighbours algorithm start')

        while changer:
            turn += 1
            layer = self.setTurnAttributes(layer, turn)
            changables = []
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
                            localChangables = [distance for distance in distanceChanges if distance in self.globalChangables and distance not in changables]
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
                                                    neighbourHoldingsCombinations = self.combine_with_constant_in_all(filteredNeighbourHoldingsIds, neighbourTargetFeatureId)
                                                    holderCombinationForChange = None
                                                    neighbourCombinationForChange = None
                                                    lenTurn = 0
                                                    holderNewTotalArea = 0
                                                    neighbourNewTotalArea = 0
                                                    totalAreaDifference = -1
                                                    for combinationLenght in range(1,len(filteredHolderHoldingsIds) + 1):
                                                        if combinationLenght <= 10:
                                                            combTurn = 0
                                                            for combination in itertools.combinations(filteredHolderHoldingsIds, combinationLenght):
                                                                if self.simply:
                                                                    if combTurn < 10000*combinationLenght and lenTurn < 20000:
                                                                        lenTurn += 1
                                                                        temporaryHolderArea = self.calculateCombinationArea(combination)
                                                                        for neighbourCombination in neighbourHoldingsCombinations:
                                                                            temporaryTargetArea = self.calculateCombinationArea(neighbourCombination)
                                                                            newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                                                                            newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                                                                            difference = abs(newHolderTotalArea-holderTotalArea)
                                                                            thresholdHolder = self.checkTotalAreaThreshold(newHolderTotalArea, holder)
                                                                            thresholdNeighbour = self.checkTotalAreaThreshold(newNeighbourTotalArea, neighbourHolder)
                                                                            if thresholdHolder and thresholdNeighbour:
                                                                                if totalAreaDifference == -1:
                                                                                    holderCombinationForChange = combination
                                                                                    neighbourCombinationForChange = neighbourCombination
                                                                                    holderNewTotalArea = newHolderTotalArea
                                                                                    neighbourNewTotalArea = newNeighbourTotalArea
                                                                                    totalAreaDifference = difference
                                                                                    combTurn += 1
                                                                                else:
                                                                                    if difference < totalAreaDifference:
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
                                                                        temporaryTargetArea = self.calculateCombinationArea(neighbourCombination)
                                                                        newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                                                                        newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                                                                        thresholdHolder = self.checkTotalAreaThreshold(newHolderTotalArea, holder)
                                                                        thresholdNeighbour = self.checkTotalAreaThreshold(newNeighbourTotalArea, neighbourHolder)
                                                                        difference = abs(newHolderTotalArea-holderTotalArea)
                                                                        if thresholdHolder and thresholdNeighbour and difference < totalAreaDifference:
                                                                            holderCombinationForChange = combination
                                                                            neighbourCombinationForChange = neighbourCombination
                                                                            holderNewTotalArea = newHolderTotalArea
                                                                            neighbourNewTotalArea = newNeighbourTotalArea
                                                                            totalAreaDifference = difference

                                                    if holderCombinationForChange and neighbourCombinationForChange:
                                                        self.setAttributeValues(layer, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                                                        if len(holderCombinationForChange) > 1 and len(neighbourCombinationForChange) > 1:
                                                            #many to many change
                                                            for holding in holderCombinationForChange:
                                                                localChangables.pop(localChangables.index(holding))
                                                                changesIds.append(holding)
                                                            for ngh in neighbourCombinationForChange:
                                                                localChangables.pop(localChangables.index(ngh))
                                                                changesIds.append(ngh)
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
                                                                localChangables.pop(localChangables.index(neighbourTargetFeatureId))
                                                                changesIds.append(neighbourTargetFeatureId)
                                                            else:
                                                                for ngh in neighbourCombinationForChange:
                                                                    localChangables.pop(localChangables.index(ngh))
                                                                    changesIds.append(ngh)
                                                                localChangables.pop(localChangables.index(holderCombinationForChange[0]))
                                                                changesIds.append(holderCombinationForChange[0])
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
                                                            holdersLocalTotalArea[holder] = holderNewTotalArea
                                                            holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                                            commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                changables.extend(changesIds)
                    elif len(seeds) == 0:
                        continue
            if turn == 1:
                changes = copy.deepcopy(self.counter)
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
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
        return layer

    def closer(self, layer, feedback, seeds=None):
        """
        DESCRIPTION: Function for make swap to get group holder's holdings closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
        INPUTS:
                layer: QgsVectorLayer
                feedback: QgsProcessingMultiStepFeedback
                seeds: List, holding ids
        OUTPUTS: QgsVectorLayer
        """
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
        else:
            self.globalChangables = self.getChangableHoldings()
            turn = 0

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
                    inDistance = self.filteredDistanceMatrix[seed]
                    distanceChanges = self.getChangableHoldings(inDistance)
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
                            for sortedCombination in targetAllCombinations:
                                targetMaxDistance = self.maxDistance(sortedCombination, targetHolderSeed, layer)
                                itsCloser = False
                                for holderCombination in holderHoldingsCombinations:
                                    holderMaxDistance = self.maxDistance(holderCombination, seed, layer)
                                    targetCloser = self.isCloser(holderMaxDistance, holderCombination, seed)
                                    holderCloser = self.isCloser(targetMaxDistance, sortedCombination, targetHolderSeed)
                                    if targetCloser and holderCloser:
                                        itsCloser = True
                                        break
                                if itsCloser:
                                    targetCombinations.append(sortedCombination)
                        else:
                            if self.onlySelected:
                                filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables]
                                targetCombinations = []
                                for combinationLenght in range(1, len(filteredLocalTargetHoldings) + 1):
                                    if combinationLenght <= 10:
                                        for combination in itertools.combinations(filteredLocalTargetHoldings, combinationLenght):
                                            if self.simply:
                                                if combTurn < 200000:
                                                    combTurn += 1
                                                else:
                                                    break
                                            sortedCombination = list(combination)
                                            sortedCombination.sort()
                                            if sortedCombination not in targetCombinations:
                                                if self.simply:
                                                    if len(targetCombinations) < 10000*combinationLenght:
                                                        targetCombinations.append(sortedCombination)
                                                    else:
                                                        break
                                                else:
                                                    targetCombinations.append(sortedCombination)
                            else:
                                continue

                        if len(holderHoldingsCombinations) > 0 and len(targetCombinations) > 0:
                            for holderCombination in holderHoldingsCombinations:
                                for targetCombination in targetCombinations:
                                    newHolderTotalArea = holderTotalArea - self.calculateCombinationArea(holderCombination) + self.calculateCombinationArea(targetCombination)
                                    if self.checkTotalAreaThreshold(newHolderTotalArea, holder):
                                        newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - self.calculateCombinationArea(targetCombination) + self.calculateCombinationArea(holderCombination)
                                        if self.checkTotalAreaThreshold(newTargetTotalArea, tempTargetHolder):
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
                        self.setAttributeValues(layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                        if len(tempHolderCombination) > 1 and len(tempTargetCombination) > 1:
                            #many to many change
                            for hold in tempHolderCombination:
                                localChangables.pop(localChangables.index(hold))
                            for ch in tempTargetCombination:
                                localChangables.pop(localChangables.index(ch))
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
                                localChangables.pop(localChangables.index(tempTargetCombination[0]))
                                commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination[0]}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            else:
                                for ch in tempTargetCombination:
                                    localChangables.pop(localChangables.index(ch))
                                localChangables.pop(localChangables.index(tempHolderCombination[0]))
                                commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            holdersLocalTotalArea[holder] = tempHolderTotalArea
                            holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                        else:
                            #one to one change
                            localChangables.pop(localChangables.index(tempTargetCombination[0]))
                            localChangables.pop(localChangables.index(tempHolderCombination[0]))
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
                changes = copy.deepcopy(self.counter)
                self.filterTouchinFeatures(layer) 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
                if changes == self.counter or abs(self.counter - changes) < int(layer.featureCount()*0.01):
                    changer = False
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                    indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                elif self.algorithmIndex == 1 or self.algorithmIndex == 2:
                    if turn == self.steps-3:
                        self.filterTouchinFeatures(layer)
                        changer = False
                elif self.algorithmIndex == 3:
                    if turn == (self.steps/2)-3:
                        self.filterTouchinFeatures(layer)
                        changer = False
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filterTouchinFeatures(layer)
            feedback.setCurrentStep(1+turn)
            feedback.pushInfo('Save turn results to the file')

        return layer

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
        layer, newId = self.createNewAttribute(layer, turn, 'id', lenght=32)
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

    def isCloser(self, thresholdDistance, featureIds, seed):
        """
        DESCRIPTION: Check if ceratin features are closer to the seed than the threshold
        INPUTS:
                thresholdDistance: Numeric
                featureIds: List, holding ids
                seed: String, holding id
        OUTPUTS: Boolean
        """
        isCloserBool = True
        for featureId in featureIds:
            distance = self.distanceMatrix[seed][featureId]
            if distance > thresholdDistance:
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
        isCloserB
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
        processing.run("native:selectbylocation", algParams)['OUTPUT']
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
        mergedLayer.commitChanges()
        finalLayer = self.createTempLayer(mergedLayer, directory, "merged")
        layer.removeSelection()
        return finalLayer
