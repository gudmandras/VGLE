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
import random, tempfile, time, os
from datetime import datetime
from . import vgle_utils, vgle_layers, vgle_features



class StatAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterField('AssignedByField', 'Holder by field',
                                                      type=QgsProcessingParameterField.Any,
                                                      parentLayerParameterName='Inputlayer'))
        self.addParameter(QgsProcessingParameterField('BalancedByField', 'Balanced by field',
                                                      type=QgsProcessingParameterField.Numeric,
                                                      parentLayerParameterName='Inputlayer',
                                                      allowMultiple=False, defaultValue=''))
        self.onlySelected = False
        self.useSingle = False
        

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return StatAlgorithm()

    def name(self):
        return 'frag_stat'

    def displayName(self):
        return self.tr('Fragmentation indicators')

    def group(self):
        return self.tr('vgle')

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("Calculates fragmentation indices for the input polygon layer.")

    def processAlgorithm(self, parameters, context, feedback):
        #import ptvsd
        #ptvsd.debug_this_thread()
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        results = {}
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context) 
        self.weight = parameters['BalancedByField']     
        out = tempfile.mkdtemp()
        tempLayer = vgle_layers.createTempLayer(inputLayer, out,
                                               'forIndices', timeStamp)
        layer, self.holderAttribute = vgle_layers.setHolderField(tempLayer, [parameters["AssignedByField"]])
        feedback.pushInfo('self.holderAttribute: ' + str(self.holderAttribute))
        self.holderAttributeType, self.holderAttributeLenght = vgle_features.getFieldProperties(layer, self.holderAttribute)
        holdersWithHoldings, holdersHoldingNumber = vgle_features.getHoldersHoldings(layer, self.holderAttribute)
        layer, self.idAttribute, holdersWithHoldings = vgle_layers.createIdField(layer, holdersWithHoldings)
        holdingsWithArea = vgle_features.getHoldingsAreas(layer, parameters["BalancedByField"], self.idAttribute)
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = vgle_utils.calculateTotalArea(self.holdersWithHoldings, self.holdingsWithArea)

        self.seeds, self.selectedHolders = vgle_utils.determineSeedPolygons(layer, self)

        feedback.pushInfo('Calculate distance matrix')
        featureThreshold = 5000
        totalFeatures = layer.featureCount()
        if totalFeatures > featureThreshold:
            self.distanceMatrix = vgle_utils.createDistanceMatrix(self, layer, nearestPoints=int(totalFeatures*0.1), simplify=True)
        else:
            self.distanceMatrix = vgle_utils.createDistanceMatrix(self, layer)
        feedback.pushInfo('Distance matrix calculated')

        data = vgle_utils.calculateStatData(self, layer, self.holderAttribute)

        copiedLayer = vgle_layers.copyLayer(layer, f"{layer.name()}_stats_{timeStamp}")
        mergedBELayer = vgle_layers.createMergedFile(self, copiedLayer, None)
        mergedBEData = vgle_utils.calculateStatData(self, mergedBELayer, self.holderAttribute)
        results['frag_stat'] = vgle_utils.createFragmentationStat(self, data, mergedBEData)

        return results

                


            




        

        

        
