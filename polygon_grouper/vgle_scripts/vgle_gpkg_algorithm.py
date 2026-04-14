__author__ = 'GOPA'
__date__ = '2024-09-05'
__copyright__ = '(C) 2024 by GOPA'
__revision__ = '$Format:%H$'

import copy
import logging
import os.path
import tempfile
import time
import gc
from datetime import datetime

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QPushButton, QWidget
from qgis.core import (QgsProject,
                       QgsProcessingContext,
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
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterFile,
                       QgsProcessingParameterFolderDestination)

from . import vgle_utils, vgle_features, vgle_methods, vgle_layers, vgle_gpkgs


class PolygonGrouperGPKG(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        #self.addParameter(QgsProcessingParameterFile('Inputlayer', 'Input layer', behavior=QgsProcessingParameterFile.File, fileFilter='GeoPackage (*.gpkg)'))
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
        self.addParameter(QgsProcessingParameterEnum('SwapToGet', 'Swap to get',
                                                     options=['Neighbours'],
                                                     allowMultiple=False, defaultValue='Neighbours'))
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]
        
        onlySelected = QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features',
                                                     defaultValue=False)
        onlySelected.setFlags(onlySelected.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(onlySelected)
        single = QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False)
        single.setFlags(single.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(single)
        strict = QgsProcessingParameterBoolean('StrictHDI', "Strict condition on Holding Distance Indicator (HDI)", defaultValue=False)
        strict.setFlags(strict.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict)
        strict2 = QgsProcessingParameterBoolean('StrictHFI', "Strict condition on Holding Fragmentation Indicator (HFI)", defaultValue=False)
        strict2.setFlags(strict2.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict2)
        stats = QgsProcessingParameterBoolean('Stats', "Generate statistics", defaultValue=False)
        stats.setFlags(stats.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(stats)
        self.version = '2026-04-15-01'

    def name(self):
        return 'polygon_grouper_gpkg'

    def displayName(self):
        return 'Polygon regrouper GPKG'

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
        return PolygonGrouperGPKG()

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        #import ptvsd
        #ptvsd.debug_this_thread()
        self.counter = 0
        results = {}
        self.steps = vgle_utils.calculateSteps(parameters['SwapToGet'])
        feedback = QgsProcessingMultiStepFeedback(self.steps, model_feedback)
        feedback.pushWarning(f"Plugin version: {self.version}\n")

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
        self.strictHDI = parameters['StrictHDI']
        self.strictHFI = parameters['StrictHFI']
        self.stats = parameters['Stats']
        parameters['Simply'] = True
        self.simply = parameters['Simply']

        filePath = self.parameterAsVectorLayer(parameters, 'Inputlayer', context).source()
        directory = os.path.dirname(filePath)
        parameters["OutputDirectory"] = directory
        if filePath[-4:].lower() != 'gpkg' and os.path.splitext(filePath)[1][:5].lower() != '.gpkg':
            feedback.reportError('The layer is not part of a GPKG')
            return {}
       
        vgle_utils.startLogging(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), parameters, timeStamp, self.version)

        # Create work file and get the starting dictionaries
        gpkg_path, tempLayerName = vgle_gpkgs.createTempLayerIntoGPKG(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), self.algorithmNames[self.algorithmIndex].lower().replace(" ", "_").replace(",", "_"), timeStamp, feedback)
        if not gpkg_path or not tempLayerName:
            feedback.reportError('Failed to create temporary layer in GPKG. Check the log for more details.')
            vgle_utils.endLogging()
            return {}
        
        self.holderAttribute = vgle_gpkgs.setHolderFieldGPKG(gpkg_path, tempLayerName, parameters["AssignedByField"])
        self.idAttribute = vgle_gpkgs.createIdFieldGPKG(gpkg_path, tempLayerName)
        holdersWithHoldings, holdersHoldingNumber = vgle_gpkgs.getHoldersHoldingsGPKG(gpkg_path, tempLayerName, self.holderAttribute, self.idAttribute)
        holdingsWithArea = vgle_gpkgs.getHoldingsAreasGPKG(gpkg_path, tempLayerName, self.weight, self.idAttribute)
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = vgle_utils.calculateTotalArea(self.holdersWithHoldings, self.holdingsWithArea)

        if parameters['Preference']:
            selectedHoldersRowIds = vgle_gpkgs.getSelectionIdsGPKG(self.parameterAsVectorLayer(parameters, 'Inputlayer', context))
        else:
            selectedHoldersRowIds = None
        self.seeds =  vgle_gpkgs.determineSeedPolygonsGPKG(self, gpkg_path, tempLayerName, selectedHoldersRowIds)

        feedback.pushInfo('Calculate distance matrix')
        featureThreshold = 5000
        totalFeatures = vgle_gpkgs.getFeatureCountGPKG(gpkg_path, tempLayerName)
        if totalFeatures > featureThreshold or self.simply:
            if self.simply:
                distanceMatrix = vgle_gpkgs.createDistanceMatrixGPKG(self, gpkg_path, tempLayerName, context, feedback, simply=self.simply)
            else:
                distanceMatrix = vgle_gpkgs.createDistanceMatrixGPKG(self, gpkg_path, tempLayerName, context, feedback, nearestPoints=int(totalFeatures*0.1), simply=self.simply)
        else:
            distanceMatrix = vgle_gpkgs.createDistanceMatrixGPKG(self, gpkg_path, tempLayerName, context, feedback)
        self.distanceMatrix = distanceMatrix
        self.distanceMatrixTable = vgle_gpkgs.saveDistanceMatrix(gpkg_path, tempLayerName, distanceMatrix)
        self.filteredDistanceMatrix = vgle_gpkgs.filterDistanceMatrix(gpkg_path, self.distanceMatrixTable, self.distance)
        feedback.pushInfo('Distance matrix calculated')

        feedback.pushInfo('Calculate total distances')
        originalTotalDistance, self.totalDistance = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, tempLayerName)
        feedback.pushInfo('Total distances calculated')

        if parameters['Stats']:
            indicatorTable = vgle_gpkgs.createStatTableGPKG(self, gpkg_path, tempLayerName)
            vgle_gpkgs.calculateStatDataGPKG(self, gpkg_path, tempLayerName, indicatorTable, 'BE', self.holderAttribute)
            
            self.interactionTable = vgle_utils.createInteractionOutput(self.holdersWithHoldings)
            
            mergedBELayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
            _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedBELayer)
            mergedBETable = vgle_gpkgs.calculateStatDataMergedGPKG(self, gpkg_path, mergedBELayer, self.holderAttribute)
            vgle_gpkgs.deleteTable(gpkg_path, mergedBELayer)
            vgle_gpkgs.calculateIndexDataGPKG(gpkg_path, indicatorTable, 'BE', mergedBETable)
            vgle_gpkgs.deleteTable(gpkg_path, mergedBETable)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            vgle_utils.endLogging()
            return {}
        # Start one of the functions
        self.turn = 0
        self.layer = (gpkg_path, tempLayerName)
        self.actualIdAttribute, self.actualHolderAttribute = copy.copy(self.idAttribute), copy.copy(self.holderAttribute)   
        oneSeedBoolean = vgle_gpkgs.checkSeedNumberGPKG(self, feedback)
        if not oneSeedBoolean:
            vgle_utils.endLogging()
            return {}
        if self.algorithmIndex == 0:
            swapedLayer, totalAreas = vgle_gpkgs.neighboursGPKG(self, feedback, context=context)
        elif self.algorithmIndex == 1:
            swapedLayer, totalAreas = vgle_gpkgs.closerGPKG(self, feedback, context=context)
        elif self.algorithmIndex == 2:
            swapedLayer, totalAreas = vgle_gpkgs.neighboursGPKG(self, feedback, context=context)
            swapedLayer, totalAreas = vgle_gpkgs.closerGPKG(self, feedback, totalAreas, context=context)
        elif self.algorithmIndex == 3:
            swapedLayer, totalAreas = vgle_gpkgs.closerGPKG(self, feedback, context=context)
            swapedLayer, totalAreas = vgle_gpkgs.neighboursGPKG(self, feedback, totalAreas, context=context)

        if swapedLayer:
            feedback.setCurrentStep(self.steps-1)
            mergedLayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
            toDeleteAttr = [attr for attr in vgle_gpkgs.getFieldNamesGPKG(gpkg_path, mergedLayer)
                            if attr not in vgle_layers.getAttributesNames(self.parameterAsVectorLayer(parameters, 'Inputlayer', context)) and attr not in [self.idAttribute, 'seed_flag', 'geom']]
            vgle_gpkgs.deleteField(gpkg_path, mergedLayer, toDeleteAttr)

            if parameters['Stats']:
                vgle_gpkgs.saveInteractionOutput1GPKG(self, self.algorithmNames[self.algorithmIndex].lower().replace(" ", "_").replace(",", "_"), timeStamp)
                vgle_gpkgs.saveInteractionOutput2GPKG(self, self.algorithmNames[self.algorithmIndex].lower().replace(" ", "_").replace(",", "_"), timeStamp)
                vgle_utils.createExchangeLog(self, self.algorithmNames[self.algorithmIndex].lower().replace(" ", "_").replace(",", "_"), timeStamp)
                

                vgle_gpkgs.calculateStatDataGPKG(self, gpkg_path, tempLayerName, indicatorTable, 'AE', self.actualHolderAttribute)
                _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedLayer)
                mergedAETable = vgle_gpkgs.calculateStatDataMergedGPKG(self, gpkg_path, mergedLayer, self.actualHolderAttribute)
                vgle_gpkgs.calculateIndexDataGPKG(gpkg_path, indicatorTable, 'CH', mergedAETable)
                vgle_gpkgs.deleteTable(gpkg_path, mergedAETable)

            #gpkg_path, layer_name = self.layer
            #cleaned_name = layer_name.replace('"', '').replace("'", '').strip()
            #uri = f'{gpkg_path}|layername={cleaned_name}'
            #feedback.pushInfo(f"URI: {uri}")
            #context.addLayerToLoadOnCompletion(
            #    uri,
            #    QgsProcessingContext.LayerDetails(layer_name, context.project())
            #)

            #mergedLayer = layer_name.replace('"', '').replace("'", '').strip()
            #uri = f'{gpkg_path}|layername={cleaned_name}'
            #feedback.pushInfo(f"URI: {uri}")
            #context.addLayerToLoadOnCompletion(
            #    uri,
            #    QgsProcessingContext.LayerDetails(mergedLayer, context.project())
            #)

                
            #vgle_layers.copyStyle(self, self.parameterAsVectorLayer(parameters, 'Inputlayer', context), swapedLayer)
            #vgle_layers.copyStyle(self, self.parameterAsVectorLayer(parameters, 'Inputlayer', context), mergedLayer)

            mainEndTime = time.time()
            logging.debug(f'Script time:{mainEndTime-mainStartTime}')

            feedback.setCurrentStep(self.steps)
            vgle_utils.endLogging()   
            results['OUTPUT'] = swapedLayer
            results['MERGED'] = mergedLayer
            return results
        else:
            if self.strictHDI or self.strictHFI:
                feedback.pushInfo('No change was made, probably due to the too strict conditions (HDI or HFI)! Try to disable these parameters and run again.') 
            elif self.counter == 0:
                feedback.pushInfo('No change was made! Try to modify the parameters and run again.') 
            else:
                feedback.reportError('Something went wrong, no change was made! See log for more details.')
            vgle_utils.endLogging()   
            return {}
