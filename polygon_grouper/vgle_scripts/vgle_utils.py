import itertools
import os
import logging
import processing
import qgis
from datetime import datetime
import subprocess
import pathlib as pa
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsApplication,
                       QgsProject,
                       QgsFeature,
                       QgsField,
                       QgsVectorLayer,
                        QgsFeatureRequest,
                       QgsVectorFileWriter)

from . import vgle_layers, vgle_features


def startLogging(layer, parameters, timeStamp):
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
    logging.debug(f'Start time: {datetime.now().strftime("%Y_%m_%d_%H_%M")}\n'
                  f'Input layer: {parameters["Inputlayer"]}\n'
                  f'Preference to selected items: {parameters["Preference"]}\n'
                  f'Use single holdings holders polygons: {parameters["Single"]}\n'
                  f'Holder atrribute(s): {parameters["AssignedByField"]}\n'
                  f'Weight attribute: {parameters["BalancedByField"]}\n'
                  f'Tolerance threshold: {parameters["Tolerance"]}\n'
                  f'Distance threshold: {parameters["DistanceThreshold"]}\n'
                  f'Simplified run: {parameters["Simply"]}\n'
                  f'Output dir: {parameters["OutputDirectory"]}')

def endLogging():
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

def calculateSteps(algorithmIndex):
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


def checkSeedNumber(seeds, feedback):
    """
    DESCRIPTION: Check every holder seed polygon number.
    If the number of the seeds for a holder is greater than one, return false
    INPUTS:
            feedback: QgsProcessingMultiStepFeedback
    OUTPUTS: Boolean
    """
    for seed in seeds.values():
        if len(seed) > 1:
            feedback.pushInfo('More than one feature preference for one holder at closer function - algorithm stop') 
            return False
    return True


def calculateTotalArea(holdersWithHoldings, holdingsWithArea):
    """
    DESCRIPTION: Create a dictionary for holder total areas
    INPUTS: 
        holdersWithHoldings: Dictionary, key: holders ids, values: List, holdings ids
        holdingsWithArea: Dictionary, key: holding id, values: Integer, area
    OUTPUTS: Dictionary, key: holders ids, values: Integer
    """
    holderTotalArea = {}
    for holder, holdings in holdersWithHoldings.items():
        totalArea = 0
        for holding in holdings:
            if holdingsWithArea[holding] != qgis.core.NULL:
                totalArea += holdingsWithArea[holding]
        holderTotalArea[holder] = totalArea
    return holderTotalArea   


def determineSeedPolygons(layer, self, preference=False, selectedFeatures=None):
    """
    DESCRIPTION: Determine one seed polygon for each holder adn store in a self dictionary
    INPUTS:
            layer: QgsVectorLayer
            preference: Boolean, the selected fature on the input layers will be the seed polygons of their holders
            selectedFeatures: QgsVectorLayer
    OUTPUTS: 
            holdersWithSeeds: Dictionary, key: holder id, values: List, holding ids
            selectedHolders: List with selected Holders Id
    """
    holdersWithSeeds = {}
    selectedHolders = []
    if preference:
        algParams = {
            'INPUT': layer,
            'PREDICATE': [3],
            'INTERSECT': selectedFeatures,
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
        selectedHolders = selectedHolders
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
    return holdersWithSeeds, selectedHolders


def createDistanceMatrix(self, layer, nearestPoints=1000, simply=False):
    #DESCRIPTION: Create a distance matrix of the input layer features
    #INPUTS:
    #        layer: QgsVectorLayer
    #OUTPUTS: Dictionary, key: holding id, values: Distionary (nested), key: holding ids, values: Float, distances
    if simply :
        if not nearestPoints:
            epsg = layer.crs().geographicCrsAuthId()[-4:]
            bufferLayer = QgsVectorLayer(f"Polygon?crs=epsg:{epsg}", "buffer", "memory")

            for seed in self.seeds:
                expression = f'"{self.idAttribute}" = \'{self.seeds[seed][0]}\''
                layer.selectByExpression(expression)
                selectedFeature = layer.selectedFeatures()
                geomBuffer = selectedFeature[0].geometry().buffer(self.distance+100, -1)
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

    algParams = {
        'INPUT': layer,
        'ALL_PARTS': False,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    centroids = processing.run("native:centroids", algParams)['OUTPUT']
    
    algParams = {
        'INPUT': centroids,
        'INPUT_FIELD': self.idAttribute,
        'TARGET': centroids,
        'TARGET_FIELD': self.idAttribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    if simply:
        if not nearestPoints:
            nearestPoints = int(totalFeatures / 10)
        algParams['MATRIX_TYPE'] = 0
        algParams['NEAREST_POINTS'] = nearestPoints
    matrix = processing.run("qgis:distancematrix", algParams)['OUTPUT']
    
    if simply:
        distanceMatrix = {}
        names = vgle_layers.getAttributesNames(matrix)
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
        names = vgle_layers.getAttributesNames(matrix)
        features = matrix.getFeatures()
        for feature in features:
            tempDict = {}
            for field in names:
                value = feature.attribute(field)
                tempDict[field] = value
            distanceMatrix[feature.attribute('ID')] = tempDict

    return distanceMatrix

def filterDistanceMatrix(distance, distanceMatrix):
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
            if value2 <= distance:
                subFilteredMatrix[key2] = value2
            else:
                break
        filteredMatrix[key] = subFilteredMatrix
    return filteredMatrix


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
    
    return totalDistances, holdingWithSeedDistance


def calculateStatData(self, layer, fieldName):
    """
    DESCRIPTION: Calculate statistics infos about a layer and fields
    INPUTS:
            layer: QgsVectorLayer
            fieldName: name of the field
    OUTPUTS: Dictionary - Holder - # of holdings - Area - Average distance
    """
    # import ptvsd
    # ptvsd.debug_this_thread()
    statData = {}
    holdersWithHoldings, holdersHoldingNumber = vgle_features.getHoldersHoldings(layer, fieldName, self.idAttribute)
    for holder, holdings in holdersWithHoldings.items():
        data = {}
        totalArea = 0
        for holding in holdings:
            if self.holdingsWithArea[holding] != qgis.core.NULL:
                totalArea += self.holdingsWithArea[holding]
        try:
            averageDistance = vgle_features.avgDistance(self, holdings, self.seeds[holder][0], layer)  
        except IndexError:
            averageDistance = 0
        except KeyError:
            averageDistance = 0
        data['ParcelNumber'] = holdersHoldingNumber[holder]
        data['TotalArea'] = totalArea
        data['AverageDistance'] = averageDistance
        statData[holder] = data

    return statData


def createInteractionOutput(holdersWithHoldings):
    """
    DESCRIPTION: Create dictionary to store interactions between holders
    INPUTS:
        holdersWithHoldings: Dictionary, key: holders ids, values: List, holdings ids
    OUTPUTS: Dictionary, key: holder ID, values: List, holder IDs
    """
    interactionTable = {}
    holders = list(holdersWithHoldings.keys()) 
    for holder in holders:
        interactionTable[holder] = {}
        for holderAgain in holders:
            interactionTable[holder][holderAgain] = 0

    return interactionTable


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


def idsForChange(holdingList, changables):
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


def combine_with_constant_in_all(elements, constant=None):
    """
    DESCRIPTION: Create of a list with nested lists of all of the possible combinations
    INPUTS:
            elements: List of strings,
            constant: String, optional (need to be in every combination)
    OUTPUTS: List
    """
    max_size = 5
    for r in range(1, len(elements) + 1):
        for combination in itertools.combinations(elements, r):
            if constant:
                # Ensure constant is always included
                combo = (constant,) + combination if constant not in combination else combination
            else:
                combo = combination

            if max_size is None or len(combo) <= max_size:
                yield combo


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
        if distance > thresholdDistance:
            isCloserBool = False
        sumDistance += distance
    if sumDistance > self.totalDistances[holder]:
        isCloserBool = False
    return isCloserBool


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
        feature['HFI (%)'] = (1 - (mergedData[holder]['ParcelNumber'] / beforeData[holder]['ParcelNumber'])) * 100
        feature['PFI (%)'] = ((afterData[holder]['TotalArea'] / beforeData[holder]['TotalArea']) - 1) * 100
        try:
            feature['HDI (%)'] = (1 - (afterData[holder]['AverageDistance'] /
                                       beforeData[holder]['AverageDistance'])) * 100
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
    beforeHoldersWithHoldings, beforeholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                             self.holderAttribute,
                                                                                             self.idAttribute)
    afterHoldersWithHoldings, afterholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                           actualHoldingId,
                                                                                           self.idAttribute)
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
        log_data.addAttributes([QgsField('Transfer to land holder ID', QVariant.String,
                                         len=self.holderAttributeLenght)])
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
                feature['Get from parcel ID'] = qgis.core.NULL
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

    fromList = []
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

    beforeHoldersWithHoldings, beforeholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                             self.holderAttribute,
                                                                                             self.idAttribute)
    afterHoldersWithHoldings, afterholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                           actualHoldingId,
                                                                                           self.idAttribute)
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

    fromList = []
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
    # import ptvsd
    # ptvsd.debug_this_thread()
    # Wrong method, but they use this
    beforeHoldersWithHoldings, beforeholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                             self.holderAttribute,
                                                                                             self.idAttribute)
    afterHoldersWithHoldings, afterholdersHoldingNumber = vgle_features.getHoldersHoldings(layer,
                                                                                           actualHoldingId,
                                                                                           self.idAttribute)
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

    with open(path, 'w') as file:
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

def update_areas_and_distances(self, holder, targetHolder, holderComb, targetComb, seed, targetSeed, localChangables):
    # update localChangables, distances, areas after swap
    for h in holderComb:
        if h in localChangables:
            localChangables.remove(h)
        self.totalDistances[holder] -= self.holdingWithSeedDistance[h]
        self.holdingWithSeedDistance[h] = self.distanceMatrix[targetSeed][h]
        self.totalDistances[targetHolder] += self.holdingWithSeedDistance[h]

    for t in targetComb:
        if t in localChangables:
            localChangables.remove(t)
        self.totalDistances[targetHolder] -= self.holdingWithSeedDistance[t]
        self.holdingWithSeedDistance[t] = self.distanceMatrix[seed][t]
        self.totalDistances[holder] += self.holdingWithSeedDistance[t]

def split_centroids_to_chunks(centroids, outputDirectory, chunkSize):
    layer = QgsVectorLayer(centroids, "centroids", "ogr")
    total = layer.featureCount()
    feature_ids = [f.id() for f in layer.getFeatures()]

    chunkFiles = []
    for i in range(0, total, chunkSize):
        chunk_ids = feature_ids[i:i + chunkSize]

        chunkFile = os.path.join(outputDirectory, f"centroids_chunk_{i}.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.fileEncoding = "UTF-8"
        options.onlySelectedFeatures = True
        context = QgsProject.instance().transformContext()

        # Select only chunk features
        layer.selectByIds(chunk_ids)
        QgsVectorFileWriter.writeAsVectorFormatV2(layer, chunkFile, context, options)
        layer.removeSelection()

        chunkFiles.append(chunkFile)

    return chunkFiles


def distance_matrix_process(input_centroids, centroids, idAttribute):
    layer = QgsVectorLayer(input_centroids, "chunkCentroids", "ogr")
    layer2 = QgsVectorLayer(centroids, "centroids", "ogr")
    algParams = {
        'INPUT': layer,
        'INPUT_FIELD': idAttribute,
        'TARGET': layer2,
        'TARGET_FIELD': idAttribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    matrix = processing.run("qgis:distancematrix", algParams)['OUTPUT']

    distanceDict = {}
    field_names = [f.name() for f in matrix.fields()]
    for feat in matrix.getFeatures():
        tempDict = {field: feat[field] for field in field_names}
        distanceDict[feat.attribute('ID')] = tempDict

    return distanceDict


def multi_distance_matrix(self, layer, outputDirectory):
    max_workers = os.cpu_count()*2
    algParams = {
        'INPUT': layer,
        'ALL_PARTS': False,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    centroids = processing.run("native:centroids", algParams)['OUTPUT']

    centroid_path = os.path.join(outputDirectory, f"{str(layer.name())}_centroids.shp")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    options.fileEncoding = "UTF-8"
    context = QgsProject.instance().transformContext()
    QgsVectorFileWriter.writeAsVectorFormatV2(centroids, centroid_path, context, options)

    cpus = max(1, os.cpu_count() - 2)
    totalFeatures = centroids.featureCount()
    chunkSize = math.ceil(totalFeatures / cpus)

    # Split layer into temp chunk files
    chunkFiles = split_centroids_to_chunks(centroid_path, outputDirectory, chunkSize)

    # Prepare tasks
    tasks = [(distance_matrix_process, chunk, centroid_path, self.idAttribute) for chunk in chunkFiles]

    # Run multiprocessing
    distanceMatrix = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(func, *args) for func, *args in tasks]

        for future in as_completed(futures):
            partial_result = future.result()
            distanceMatrix.update(partial_result)

    return distanceMatrix