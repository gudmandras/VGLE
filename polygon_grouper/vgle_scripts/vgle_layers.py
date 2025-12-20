import os
import uuid
import tempfile
import qgis
import processing

from qgis.PyQt.QtCore import QVariant
from PyQt5.QtCore import QCoreApplication
from qgis.core import (QgsVectorFileWriter,
                       QgsVectorLayer,
                       QgsProject,
                       QgsField)


def createTempLayer(layer, directory, postfix, timeStamp=None):
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


def setHolderField(layer, field):
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
        layer, fieldName = setTempHolderField(layer)
        layer = setTempHolderValue(layer, fieldName, field)
        return layer, fieldName


def setTempHolderField(layer):
    """
    DESCRIPTION: Create a new field for the holder
    INPUTS:
            layer: QgsVectorLayer
    OUTPUTS: QgsVectorLayer, String
    """
    fieldName = 'holder_id'
    layerAttributes = getAttributesNames(layer)
    if fieldName in layerAttributes:
        counter = 0
        while fieldName in layerAttributes:
            fieldName = f"{fieldName}{counter}"
            counter += 1
    layer.startEditing()
    dataProvider = layer.dataProvider()
    dataProvider.addAttributes([QgsField(fieldName, QVariant.Int)])
    layer.updateFields()
    return layer, fieldName


def setTempHolderValue(layer, fieldName, attributes):
    """
    DESCRIPTION: Set holder attribute values, if more field has received, create a new field, with a combined Id values
    INPUTS:
            layer: QgsVectorLayer
            fieldName: String, field name
            attributes: List, name of the attribute field, which hold the holder values
    OUTPUTS: QgsVectorLayer
    """
    # import ptvsd
    # ptvsd.debug_this_thread()
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
        for turned, tempValue in enumerate(tempListWithValues):
            if len(tempListWithValues) > 1:
                if tempValue != '' and tempValue != qgis.core.NULL:
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
                        expression += '"{field}"=\'{value}\' AND '.format(field=attributes[turn],
                                                                          value=str(uniqueValue))
                    else:
                        expression += '"{field}"=\'{value}\''.format(field=attributes[turn],
                                                                     value=str(uniqueValue))
                else:
                    if turn+1 != len(uniqueValueList):
                        expression += '"{field}"={value} AND '.format(field=attributes[turn],
                                                                      value=str(uniqueValue))
                    else:
                        expression += '"{field}"={value}'.format(field=attributes[turn],
                                                                 value=str(uniqueValue))
        layer.selectByExpression(expression)
        selectedFeatures = layer.selectedFeatures()
        if layer.selectedFeatureCount() > 0:
            counter += 1
            if not layer.isEditable():
                layer.startEditing()
            for feature in selectedFeatures:
                layer.changeAttributeValue(feature.id(), fieldNameId, counter)
            layer.removeSelection()
    layer.commitChanges()

    return layer


def getAttributesNames(layer):
    """
    DESCRIPTION: Gets the attribute names of a layer
    INPUTS:
            layer: QgsVectorLayer
    OUTPUTS: List
    """
    attributes = [field.name() for field in layer.fields()]
    return attributes


def createIdField(layer, holders):
    """
    DESCRIPTION: Create a new field for the holding ids
    INPUTS:
            layer: QgsVectorLayer
            holders: Dictionary, key: holders ids, values: List, holdings ids
    OUTPUTS: QgsVectorLayer, String, Dictionary
    """
    fieldName = 'temp_id'
    layerAttributes = getAttributesNames(layer)
    if fieldName in layerAttributes:
        counter = 0
        while fieldName in layerAttributes:
            fieldName = f"{fieldName}{counter}"
            counter += 1
    if not layer.isEditable():
        layer.startEditing()
    dataProvider = layer.dataProvider()
    dataProvider.addAttributes([QgsField(fieldName, QVariant.String, len=10)])
    layer.updateFields()
    layer, holdersWithHoldingId = setIdField(layer, fieldName, holders)
    return layer, fieldName, holdersWithHoldingId


def setIdField(layer, attribute, holders):
    """
    DESCRIPTION: Set the holding Id field
    INPUTS:
            layer: QgsVectorLayer
            attribute: String, name of the Id attribute field
            holders: Dictionary, key: holders ids, values: List, holdings ids
    OUTPUTS: QgsVectorLayer, Dictionary
    """
    attributeId = getAttributesNames(layer).index(attribute)
    holdersWithHoldingId = {}
    if not layer.isEditable():
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


def setTurnAttributes(self, layer, turn):
    """
    DESCRIPTION: Set the values of a fields in a layer
    INPUTS:
            layer: QgsVectorLayer
            turn: Integer
    OUTPUTS: QgsVectorLayer
    """
    layer, newId = createNewAttribute(layer, turn, 'id', lenght=255)
    layer, newHolder = createNewAttribute(layer, turn, 'holder', typer=self.holderAttributeType,
                                          lenght=self.holderAttributeLenght)
    if turn == 1:
        layer.startEditing()
        for feature in layer.getFeatures():
            layer.changeAttributeValue(feature.id(), getAttributesNames(layer).index(newHolder),
                                       str(feature.attribute(self.holderAttribute)))
        layer.commitChanges()
    else:
        layer.startEditing()
        for feature in layer.getFeatures():
            layer.changeAttributeValue(feature.id(), getAttributesNames(layer).index(newHolder),
                                       str(feature.attribute(self.actualHolderAttribute)))
        layer.commitChanges()
    return newId, newHolder, layer


def createNewAttribute(layer, turn, adj, typer=QVariant.String, lenght=50):
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
    layerAttributes = getAttributesNames(layer)
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


def setNewAttribute(idAttribute, layer, featureId, newValue, field):
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
    expression += f'"{idAttribute}" = \'{featureId}\''
    layer.selectByExpression(expression)
    index = getAttributesNames(layer).index(field)
    layer.startEditing()
    for feature in layer.selectedFeatures():
        layer.changeAttributeValue(feature.id(), index, newValue)
    layer.commitChanges()


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
        # many to many change
        for hold in tempHolderCombination:
            setNewAttribute(self.idAttribute, layer, hold, ','.join(tempHolderCombination), self.actualIdAttribute)
            setNewAttribute(self.idAttribute, layer, hold, targetHolder, self.actualHolderAttribute)
            self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(hold))
            self.holdersWithHoldings[targetHolder].append(hold)
        for ch in tempTargetCombination:
            setNewAttribute(self.idAttribute, layer, ch, ','.join(tempTargetCombination), self.actualIdAttribute)
            setNewAttribute(self.idAttribute, layer, ch, holder, self.actualHolderAttribute)
            self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(ch))
            self.holdersWithHoldings[holder].append(ch)
        self.counter += 1
    elif len(tempHolderCombination) > 1 and len(tempTargetCombination) == 1 or \
            len(tempHolderCombination) == 1 and len(tempTargetCombination) > 1:
        # many to one change
        if len(tempHolderCombination) > 1:
            for hold in tempHolderCombination:
                setNewAttribute(self.idAttribute, layer, hold, tempTargetCombination[0], self.actualIdAttribute)
                setNewAttribute(self.idAttribute, layer, hold, targetHolder, self.actualHolderAttribute)
                self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(hold))
                self.holdersWithHoldings[targetHolder].append(hold)
            setNewAttribute(self.idAttribute, layer, tempTargetCombination[0], holder, self.actualHolderAttribute)
            setNewAttribute(self.idAttribute, layer, tempTargetCombination[0], ','.join(tempHolderCombination),
                                 self.actualIdAttribute)
            self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].
                                                       index(tempTargetCombination[0]))
            self.holdersWithHoldings[holder].append(tempTargetCombination[0])
            self.counter += 1
        else:
            for ch in tempTargetCombination:
                setNewAttribute(self.idAttribute, layer, ch, tempHolderCombination[0], self.actualIdAttribute)
                setNewAttribute(self.idAttribute, layer, ch, holder, self.actualHolderAttribute)
                self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].index(ch))
                self.holdersWithHoldings[holder].append(ch)
            setNewAttribute(self.idAttribute, layer, tempHolderCombination[0], targetHolder,
                                 self.actualHolderAttribute)
            setNewAttribute(self.idAttribute, layer, tempHolderCombination[0], ','.join(tempTargetCombination),
                                 self.actualIdAttribute)
            self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(tempHolderCombination[0]))
            self.holdersWithHoldings[targetHolder].append(tempHolderCombination[0])
            self.counter += 1
    else:
        # one to one change
        setNewAttribute(self.idAttribute, layer, tempHolderCombination[0], tempTargetCombination[0],
                             self.actualIdAttribute)
        setNewAttribute(self.idAttribute, layer, tempHolderCombination[0], targetHolder,
                             self.actualHolderAttribute)
        setNewAttribute(self.idAttribute, layer, tempTargetCombination[0], tempHolderCombination[0],
                             self.actualIdAttribute)
        setNewAttribute(self.idAttribute, layer, tempTargetCombination[0], holder, self.actualHolderAttribute)
        self.holdersWithHoldings[targetHolder].pop(self.holdersWithHoldings[targetHolder].
                                                   index(tempTargetCombination[0]))
        self.holdersWithHoldings[holder].append(tempTargetCombination[0])
        self.holdersWithHoldings[holder].pop(self.holdersWithHoldings[holder].index(tempHolderCombination[0]))
        self.holdersWithHoldings[targetHolder].append(tempHolderCombination[0])
        self.counter += 1  


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
    index = getAttributesNames(mergedLayer).index(self.weight)
    mergedLayer.startEditing()
    for feature in mergedLayer.getFeatures():
        newValue = feature.geometry().area()/10000
        mergedLayer.changeAttributeValue(feature.id(), index, newValue)
    mergedLayer.commitChanges()
    finalLayer = createTempLayer(mergedLayer, directory, "merged")
    layer.removeSelection()
    return finalLayer  


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

def copyStyle(self, templateLayer, targetLayer):
    if templateLayer.renderer():
        new_renderer = templateLayer.renderer().clone()
        targetLayer.setRenderer(new_renderer)

    if templateLayer.labelsEnabled():
        targetLayer.setLabeling(templateLayer.labeling().clone())
        targetLayer.setLabelsEnabled(True)

    renderer = targetLayer.renderer()
    if hasattr(renderer, 'setClassAttribute'):
        lastHolderAttribute = int(self.actualHolderAttribute.split('_')[0])
        
        if lastHolderAttribute == self.steps - 2:
            offset = 2 if lastHolderAttribute >= 10 else 1
            holderAttribute = str(lastHolderAttribute) + self.actualHolderAttribute[offset:]
        else:
            offset = 2 if lastHolderAttribute >= 10 else 1
            holderAttribute = str(lastHolderAttribute - 1) + self.actualHolderAttribute[offset:]
            
        renderer.setClassAttribute(holderAttribute)
    
    targetLayer.triggerRepaint()
    if hasattr(self, 'iface'):
        self.iface.layerTreeView().refreshLayerLegend(targetLayer)
"""    
def copyStyle(self, templateLayer, targetLayer):
    tmp_qml = os.path.join(tempfile.gettempdir(), 'temp_style.qml')
    templateLayer.saveNamedStyle(tmp_qml)

    # Load style into target layer
    targetLayer.loadNamedStyle(tmp_qml)
    targetLayer.triggerRepaint()

    # try to reconfigure attr value
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
    QCoreApplication.processEvents()
    try:
        os.remove(tmp_qml)
    except OSError as e:
        print(f"Error: {tmp_qml} : {e.strerror}")
"""