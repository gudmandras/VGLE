__author__ = 'GOPA'
__date__ = '2024-09-05'
__copyright__ = '(C) 2024 by GOPA'
__revision__ = '$Format:%H$'

import copy
import logging
import os.path
import tempfile
import time
from datetime import datetime

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QPushButton, QWidget
from qgis.core import (QgsProject,
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

from .vgle_scripts import vgle_utils, vgle_features, vgle_methods, vgle_layers


class PolygonGrouper(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features',
                                                        defaultValue=False))
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
        
        onlySelected = QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features',
                                                     defaultValue=False)
        onlySelected.setFlags(onlySelected.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(onlySelected)
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
        try:
            with open(os.path.join(os.path.dirname(__file__), 'vgle_scripts', 'shorthelp.txt'), 'r',
                      encoding='utf-8') as file:
                return file.read()
        except FileNotFoundError:
            return "<html><body><p>Description file not found.</p></body></html>"
        except Exception as e:
            return f"<html><body><p>Error reading description file: {e}</p></body></html>"

    def createInstance(self):
        return PolygonGrouper()

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        #import ptvsd
        #ptvsd.debug_this_thread()
        self.counter = 0
        results = {}
        self.steps = vgle_utils.calculateSteps(parameters['SwapToGet'])
        feedback = QgsProcessingMultiStepFeedback(self.steps, model_feedback)

        if parameters['OnlySelected'] and parameters['Preference'] is not True:
            feedback.reportError(f"'Only use the selected features' parameters works only with "
                              f"'Give preference for the selected features parameter'. "
                              f"'Give preference for the selected features parameter' is enabled")
            parameters['Preference'] = True

        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        mainStartTime = time.time()

        # Get inputs
        self.weight = parameters['BalancedByField']
        self.tolerance = parameters['Tolerance']
        self.distance = parameters['DistanceThreshold']
        self.useSingle = parameters['Single']
        self.onlySelected = parameters['OnlySelected']
        self.algorithmIndex = parameters['SwapToGet']
        self.simply = parameters['Simply']
        self.strict = parameters['Strict']
        self.stats = parameters['Stats']
        inputLayer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
        context.temporaryLayerStore().addMapLayer(inputLayer)
        if parameters['OutputDirectory'] == 'TEMPORARY_OUTPUT':
            parameters['OutputDirectory'] = tempfile.mkdtemp()
       
        vgle_utils.startLogging(inputLayer, parameters, timeStamp)
        # Create work file and get the starting dictionaries
        tempLayer = vgle_layers.createTempLayer(inputLayer, parameters["OutputDirectory"],
                                                self.algorithmNames[self.algorithmIndex].lower(), timeStamp)
        layer, self.holderAttribute = vgle_layers.setHolderField(tempLayer, parameters["AssignedByField"])
        context.temporaryLayerStore().addMapLayer(layer)
        self.holderAttributeType, self.holderAttributeLenght = \
            vgle_features.getFieldProperties(tempLayer, self.holderAttribute)
        holdersWithHoldings, holdersHoldingNumber = vgle_features.getHoldersHoldings(layer, self.holderAttribute)
        layer, self.idAttribute, holdersWithHoldings = vgle_layers.createIdField(layer, holdersWithHoldings)
        layer.dataProvider().createSpatialIndex()
        holdingsWithArea = vgle_features.getHoldingsAreas(layer, parameters["BalancedByField"], self.idAttribute)
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = vgle_utils.calculateTotalArea(self.holdersWithHoldings, self.holdingsWithArea)

        if parameters['Preference']:
            selectedFeatures = vgle_features.getSelectedFeatures(inputLayer)
            self.seeds, self.selectedHolders = vgle_utils.determineSeedPolygons(layer, self,
                                                                                parameters['Preference'],
                                                                                selectedFeatures)
        else:
            self.seeds, self.selectedHolders = vgle_utils.determineSeedPolygons(layer, self)

        feedback.pushInfo('Calculate distance matrix')
        featureThreshold = 5000
        totalFeatures = layer.featureCount()
        if totalFeatures > featureThreshold or self.simply:
            if self.simply:
                self.distanceMatrix = vgle_utils.createDistanceMatrix(self, layer, simply=self.simply)
                self.filteredDistanceMatrix = self.distanceMatrix.copy()
            else:
                self.distanceMatrix = vgle_utils.createDistanceMatrix(self, layer, nearestPoints=int(totalFeatures*0.1), simply=self.simply)
                self.filteredDistanceMatrix = vgle_utils.filterDistanceMatrix(self.distance, self.distanceMatrix)
        else:
            self.distanceMatrix = vgle_utils.createDistanceMatrix(self, layer)
            self.filteredDistanceMatrix = vgle_utils.filterDistanceMatrix(self.distance, self.distanceMatrix)
        feedback.pushInfo('Distance matrix calculated')

        feedback.pushInfo('Calculate total distances')
        self.totalDistances, self.holdingWithSeedDistance = vgle_utils.calculateTotalDistances(self, layer)
        feedback.pushInfo('Total distances calculated')

        if parameters['Stats']:
            beforeData = vgle_utils.calculateStatData(self, layer, self.holderAttribute)
            self.interactionTable = vgle_utils.createInteractionOutput(self.holdersWithHoldings)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            vgle_utils.endLogging()
            return {}
        # Start one of the functions
        if self.algorithmIndex == 0:
            swapedLayer, totalAreas = vgle_methods.neighbours(self, layer, feedback, context=context)
        elif self.algorithmIndex == 1:
            oneSeedBoolean = vgle_utils.checkSeedNumber(self.seeds, feedback)
            if oneSeedBoolean:
                swapedLayer, totalAreas = vgle_methods.closer(self, layer, feedback, context=context)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 2:
            oneSeedBoolean = vgle_utils.checkSeedNumber(self.seeds, feedback)
            if oneSeedBoolean:
                originalSeeds = copy.deepcopy(self.seeds)
                swapedLayer, totalAreas = vgle_methods.neighbours(self, layer, feedback, context=context)
                swapedLayer, totalAreas = vgle_methods.closer(self, swapedLayer, feedback, originalSeeds, totalAreas, context=context)
            else:
                swapedLayer = False
        elif self.algorithmIndex == 3:
            oneSeedBoolean = vgle_utils.checkSeedNumber(self.seeds, feedback)
            if oneSeedBoolean:
                swapedLayer, totalAreas = vgle_methods.closer(self, layer, feedback, context=context)
                swapedLayer, totalAreas = vgle_methods.neighbours(self, swapedLayer, feedback, totalAreas, context=contex)
            else:
                swapedLayer = False
        #elif self.algorithmIndex == 4:
        #    swapedLayer = vgle_methods.hybrid_method(self, layer, feedback)
        # Save results and create merged file
        context.temporaryLayerStore().addMapLayer(swapedLayer)
        if swapedLayer:
            feedback.setCurrentStep(self.steps-1)

            swapedLayer.commitChanges()
            swapedLayer.removeSelection()
            QgsProject.instance().addMapLayer(swapedLayer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, swapedLayer)

            mergedLayer = vgle_layers.createMergedFile(self, swapedLayer, parameters["OutputDirectory"])
            toDeleteAttr = [attr for attr in vgle_layers.getAttributesNames(mergedLayer)
                            if attr not in vgle_layers.getAttributesNames(inputLayer)]
            vgle_layers.cleanMergedLayer(self, toDeleteAttr, mergedLayer)

            vgle_layers.copyStyle(self, inputLayer, swapedLayer)
            vgle_layers.copyStyle(self, inputLayer, mergedLayer)

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
                afterData = vgle_utils.calculateStatData(self, swapedLayer, attributeName)
                mergedData = vgle_utils.calculateStatData(self, mergedLayer, attributeName)
                try:
                    vgle_utils.saveInteractionOutput2(self, swapedLayer, attributeName)
                    vgle_utils.createIndicesStat(self, beforeData, afterData, mergedData)
                    vgle_utils.createExchangeLog(self, swapedLayer, attributeName)
                    vgle_utils.saveInteractionOutput(self)
                except Exception as e:
                    feedback.pushInfo(f"No statistics generation due to no changes made: {e}")
                # self.saveInteractionOutputGOPA(self, os.path.join(parameters["OutputDirectory"],
                # f"{str(swapedLayer.source()[:-4])}_interactions.csv"), swapedLayer, attributeName)
                # self.calculateShapeIndexes(self, swapedLayer, mergedLayer)

            swapedLayer.commitChanges()

            mainEndTime = time.time()
            logging.debug(f'Script time:{mainEndTime-mainStartTime}')

            feedback.setCurrentStep(self.steps)
            vgle_utils.endLogging()   
            results['OUTPUT'] = swapedLayer
            results['MERGED'] = mergedLayer
            return results
        else:
            feedback.pushInfo('No change was made!') 
            vgle_utils.endLogging()   
            return {}