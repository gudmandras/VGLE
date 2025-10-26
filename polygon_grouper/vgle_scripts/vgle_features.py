import processing
import math
import statistics
import qgis
import qgis.core
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from qgis.core import (Qgis,
                       QgsPoint,
                       QgsGeometry,
                       QgsProject,
                       QgsProcessing,
                       QgsProcessingFeatureSourceDefinition,
                       QgsFeature,
                       QgsField,
                       QgsVectorLayer,
                       QgsFeatureRequest,
                       QgsWkbTypes)


def getFieldProperties(layer, fieldName):
    """
    DESCRIPTION: Give back of a field type and length
    INPUTS:
            layer: QgsVectorLayer
            fieldName: String, name of the field
    OUTPUTS: ogr type, integer
    """
    for field in layer.fields():
        if field.name() == fieldName:
            return field.type(), field.length()


def getSelectedFeatures(inputLayer):
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
    selectedFeatures = processing.run("native:saveselectedfeatures", algParams)["OUTPUT"]
    return selectedFeatures


def getHoldersHoldings(layer, holderAttribute, attributeName=None):
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
        holder = feature.attribute(holderAttribute)
        if attributeName:
            featureId = feature.attribute(attributeName)
        else:
            featureId = feature.id()
        if holder != qgis.core.NULL:
            if holder in list(holdersWithHoldings.keys()):
                holdersWithHoldings[holder].append(featureId)
                holdersHoldingNumber[holder] = holdersHoldingNumber[holder] + 1
            else:
                holdersWithHoldings[holder] = [featureId]
                holdersHoldingNumber[holder] = 1
    return holdersWithHoldings, holdersHoldingNumber


def getHoldingsAreas(layer, areaId, idAttribute):
    """
    DESCRIPTION: Create a dictionary for holding and its area
    INPUTS:
            layer: QgsVectorLayer
            areaId: Integer, ID of the polygon feature
            idAttribute: Attribute name of the ID field
    OUTPUTS: Dictionary, key: holding id, values: Integer, area
    """
    holdingsWithAreas = {}
    features = layer.getFeatures()
    for feature in features:
        area = feature.attribute(areaId)
        if area == qgis.core.NULL:
            area = feature.geometry().area() / 10000
        holdingId = feature.attribute(idAttribute)
        holdingsWithAreas[holdingId] = area
    return holdingsWithAreas


def getNeighbours(idAttribute, layer, seed):
    """
    DESCRIPTION: Get neighbours holdings of a certain polygon
    INPUTS:
            layer: QgsVectorLayer
            seed: holding id of the holder's seed polygon
    OUTPUTS:
            neighboursIds: List, holding ids
            neighbours: QgsVectorLayer
    """
    expression = f'"{idAttribute}" = \'{seed}\''
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
    neighboursIds = [neighboursFeature.attribute(idAttribute) for neighboursFeature in neighboursFeatures]

    return neighboursIds, neighbours


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
            distance = calculateDistance(self, featureIds, seed, layer)
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
            distance = calculateDistance(self, featureIds, seed, layer)
            sumDistance += distance
    return sumDistance / divider

def calculateDistance(self, featureId, seed, layer):
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

    return distance
    


def filterTouchingFeatures(self, layer, toSeed=False):
    """
    DESCRIPTION: Determine, if a holder holding touches its seed polygon. If true, filter it, from the changables,
    and can be mark as seed
    INPUTS:
            layer: QgsVectorLayer
            toSeed: Boolean
    OUTPUTS: None
    """
    algParams = {
        'INPUT': layer,
        'FIELD': [self.actualHolderAttribute],
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    dissolvedLayer = processing.run("native:dissolve", algParams)['OUTPUT']

    algParams = {
        'INPUT': dissolvedLayer,
        'OUTPUT': 'TEMPORARY_OUTPUT'
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
        'PREDICATE': [1],
        'METHOD': 0,
        'INTERSECT': QgsProcessingFeatureSourceDefinition(layer.source(), selectedFeaturesOnly=True, featureLimit=-1,
                                                          geometryCheck=QgsFeatureRequest.GeometryAbortOnInvalid),
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    mergedSeed = processing.run("native:extractbylocation", algParams)['OUTPUT']

    algParams = {
        'INPUT': layer,
        'PREDICATE': [6],
        'METHOD': 0,
        'INTERSECT': mergedSeed,
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


def checkShape(self, layer, seed, holdings, holderCombination, sortedCombination):
    # import ptvsd
    # ptvsd.debug_this_thread()
    algParams = {
        'INPUT': layer,
        'FIELD': [self.actualHolderAttribute],
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    dissolvedLayer = processing.run("native:dissolve", algParams)['OUTPUT']

    algParams = {
        'INPUT': dissolvedLayer,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    simpliedLayer = processing.run("native:multiparttosingleparts", algParams)['OUTPUT']

    algParams = {
        'INPUT': simpliedLayer,
        'METHOD': 0,
        'TOLERANCE': 5,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    simpliedLayer = processing.run("native:simplifygeometries", algParams)['OUTPUT']

    expression = f'"{self.idAttribute}" = \'{seed}\''
    layer.selectByExpression(expression)

    algParams = {
        'INPUT': layer,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    selectedFeatures = processing.run('native:saveselectedfeatures', algParams)['OUTPUT']

    algParams = {
        'INPUT': simpliedLayer,
        'PREDICATE': [1, 3, 5],
        'METHOD': 0,
        'INTERSECT': selectedFeatures,
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
        originalShapeSimilarity = square_simmilarity(mergedSeedGeometry)
        newShapeSimilarity = square_simmilarity(newSeedGeometry)
        if newShapeSimilarity >= originalShapeSimilarity:
            return True
        else:
            return False


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
            if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
                # Handle single Polygon
                sideLengths.extend(calculatePolygonSides(geometry.asPolygon()[0]))
                angles.extend(calculateAngles(geometry.asPolygon()[0]))
            elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
                # Handle MultiPolygon (a collection of polygons)
                for polygon in geometry.asMultiPolygon():
                    sideLengths.extend(calculatePolygonSides(polygon))
                    angles.extend(calculateAngles(polygon))

            feature['Length of sides - before'] = sum(sideLengths)
            feature['Acute angles - before'] = len([angle for angle in angles if angle < 90])
            feature['Reflex angles - before'] = len([angle for angle in angles if 180 < angle < 360])
            feature['Boundary points - before'] = len(angles)

            algParams = {
                'INPUT': swapedLayer,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            selectedFeatures = processing.run("native:saveselectedfeatures", algParams)["OUTPUT"]

            algParams = {
                'INPUT': mergedLayer,
                'PREDICATE': [1, 5],
                'INTERSECT': selectedFeatures,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            processing.run("native:selectbylocation", algParams)
            selectedFeatures = mergedLayer.selectedFeatures()[0]
            geometry = selectedFeatures.geometry()

            sideLengths = []
            angles = []
            if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
                # Handle single Polygon
                sideLengths.extend(calculatePolygonSides(geometry.asPolygon()[0]))
                angles.extend(calculateAngles(geometry.asPolygon()[0]))
            elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
                # Handle MultiPolygon (a collection of polygons)
                for polygon in geometry.asMultiPolygon():
                    sideLengths.extend(calculatePolygonSides(polygon))
                    angles.extend(calculateAngles(polygon))

            feature['Length of sides - after'] = sum(sideLengths)
            feature['Acute angles - after'] = len([angle for angle in angles if angle < 90])
            feature['Reflex angles - after'] = len([angle for angle in angles if 180 < angle < 360])
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


def calculatePolygonSides(ring):
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


def calculate_angle(v1, v2):
    # Dot product of vectors v1 and v2
    dot_product = v1.x() * v2.x() + v1.y() * v2.y()

    # Magnitudes (lengths) of the vectors
    mag_v1 = math.sqrt(v1.x() ** 2 + v1.y() ** 2)
    mag_v2 = math.sqrt(v2.x() ** 2 + v2.y() ** 2)

    # Calculate cosine of the angle using the dot product formula
    cos_angle = dot_product / (mag_v1 * mag_v2)

    # Get the angle in radians and convert to degrees
    cos_angle = max(-1, min(1, cos_angle))
    angle_rad = math.acos(cos_angle)
    angle_deg = math.degrees(angle_rad)

    return angle_deg


def calculateAngles(polygon):
    angles = []

    if type(polygon[0]) == list:
        polygon = polygon[0]

    # Loop through each vertex of the polygon
    for i in range(len(polygon)):
        # Get previous, current, and next points to form vectors
        if i == 0:
            prevPoint = polygon[i - 2]  # Previous point (circular)
            currentPoint = polygon[i]  # Current point
            nextPoint = polygon[i + 1]  # Next point (circular)
        elif i == len(polygon) - 1:
            prevPoint = polygon[i - 1]  # Previous point (circular)
            currentPoint = polygon[i]  # Current point
            nextPoint = polygon[1]  # Next point (circular)
        else:
            prevPoint = polygon[i - 1]  # Previous point (circular)
            currentPoint = polygon[i]  # Current point
            nextPoint = polygon[i + 1]  # Next point (circular)

        # Create vectors: 
        # Vector from current to previous point
        v1 = QgsPoint(prevPoint.x() - currentPoint.x(), prevPoint.y() - currentPoint.y())
        # Vector from current to next point
        v2 = QgsPoint(nextPoint.x() - currentPoint.x(), nextPoint.y() - currentPoint.y())

        # Calculate the angle between the two vectors
        angle = calculate_angle(v1, v2)
        angles.append(angle)

    return angles


def square_simmilarity(geometry):
    sideLengths = []
    angles = []

    if geometry.wkbType() == QgsWkbTypes.PolygonGeometry:
        # Handle single Polygon
        sideLengths.extend(calculatePolygonSides(geometry.asPolygon()[0]))
        angles.extend(calculateAngles(geometry.asPolygon()[0]))
    elif geometry.wkbType() == QgsWkbTypes.MultiPolygon:
        # Handle MultiPolygon (a collection of polygons)
        for polygon in geometry.asMultiPolygon():
            sideLengths.extend(calculatePolygonSides(polygon))
            angles.extend(calculateAngles(polygon))
    else:
        # Handle single Polygon
        sideLengths.extend(calculatePolygonSides(geometry.asPolygon()[0]))
        angles.extend(calculateAngles(geometry.asPolygon()[0]))
    averageSide = sum(sideLengths) / len(sideLengths)
    lenghtVariation = statistics.stdev(sideLengths) / averageSide

    angleVariation = statistics.stdev(angles) / 90

    similarity = max(0, 1 - (lenghtVariation + angleVariation) / 2)

    return similarity


def holdingsClosestToSeed(self, layer, holdings, seed, seedList):
    closestHoldings = []
    for holding in holdings:
        closestSeed = None
        closestDistance = None
        for sed in seedList:
            if closestSeed and closestDistance:
                try:
                    distance = self.distanceMatrix[sed][holding]
                except KeyError:
                    distance = calculateDistance(self, holding, sed, layer)
                if distance < closestDistance:
                    closestSeed = sed
                    closestDistance = distance
            else:
                try:
                    distance = self.distanceMatrix[sed][holding]
                except KeyError:
                    distance = calculateDistance(self, holding, sed, layer)
                closestSeed = sed
                closestDistance = distance
        if closestSeed == seed:
            closestHoldings.append(holding)
    return closestHoldings
