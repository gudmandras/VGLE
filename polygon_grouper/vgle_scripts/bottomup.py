import copy
import logging
import os.path
import tempfile
import time
import gc
from datetime import datetime

from PyQt5.QtCore import QTimer, QEventLoop
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

def wait(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec_()

class BottomUpAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features',
                                                        defaultValue=True))
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
        self.algorithmNames = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]
        
        onlySelected = QgsProcessingParameterBoolean('OnlySelected', 'Only use the selected features',
                                                     defaultValue=False)
        onlySelected.setFlags(onlySelected.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(onlySelected)
        single = QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False)
        single.setFlags(single.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(single)
        strict = QgsProcessingParameterBoolean('StrictHDI', "Strict condition on polygons distances per holder", defaultValue=False)
        strict.setFlags(strict.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict)
        strict2 = QgsProcessingParameterBoolean('StrictHFI', "Strict condition on polygons number per holder", defaultValue=False)
        strict2.setFlags(strict2.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(strict2)
        stats = QgsProcessingParameterBoolean('Stats', "Generate statistics", defaultValue=False)
        stats.setFlags(stats.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(stats)
        simplfy = QgsProcessingParameterNumber('Simply', "Number of holding combinations to analyze:",
                                                type=QgsProcessingParameterNumber.Integer,
                                                minValue=0, defaultValue=0)
        simplfy.setFlags(simplfy.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(simplfy)
        holdersTreshold = QgsProcessingParameterNumber('holdersThreshold', 'Maximal number of the holder in the group',
                                                       type=QgsProcessingParameterNumber.Integer,
                                                       minValue=0, defaultValue=20)
        holdersTreshold.setFlags(holdersTreshold.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(holdersTreshold)
        resultOption = QgsProcessingParameterEnum('resultOption', 'Desired way of the result',
                                                       options=['Only the group in the result', 'Flag the group in the result'],
                                                       allowMultiple=False, defaultValue='Only the group in the result')
        resultOption.setFlags(resultOption.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(resultOption)

        self.version = '2026-07-27-01'

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

    def processAlgorithm(self, parameters, context, model_feedback):
        #import ptvsd
        #ptvsd.debug_this_thread()
        parameters['Preference'] = True
        self.counter = 0
        results = {}

        if not vgle_gpkgs.checkTableName(parameters, model_feedback):
            return {}

        self.steps = vgle_utils.calculateSteps(parameters['SwapToGet'])
        feedback = QgsProcessingMultiStepFeedback(self.steps, model_feedback)
        feedback.pushWarning(f"Plugin version: {self.version}\n")

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
        self.simply = parameters['Simply']
        self.groupSize = parameters['holdersThreshold']
        self.resultType = parameters['resultOption']

        filePath = self.parameterAsVectorLayer(parameters, 'Inputlayer', context).source()
        directory = os.path.dirname(filePath)
        parameters["OutputDirectory"] = directory
        if filePath[-4:].lower() != 'gpkg' and os.path.splitext(filePath)[1][:5].lower() != '.gpkg':
            feedback.reportError('The layer is not part of a GPKG')
            return {}
       
        vgle_utils.startLogging(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), parameters, timeStamp, self.version)

        selectedHoldersRowIds = vgle_gpkgs.getSelectionIdsGPKG(self.parameterAsVectorLayer(parameters, 'Inputlayer', context))
        if len(selectedHoldersRowIds) == 0:
            feedback.reportError('Please select features to process', fatalError=True)
            return {}
        elif len(selectedHoldersRowIds) > self.groupSize:
            feedback.reportError('More selected holder than group size! Increase the group size or deselect holders', fatalError=True)
            return {}

        # Create work file and get the starting dictionaries
        gpkg_path, tempLayerName = vgle_gpkgs.createTempLayerIntoGPKG(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), self.algorithmNames[self.algorithmIndex].lower().replace(" ", "_").replace(",", "_"), timeStamp, feedback)
        if not gpkg_path or not tempLayerName:
            feedback.reportError('Failed to create temporary layer in GPKG. Check the log for more details.')
            vgle_utils.endLogging()
            return {}
        
        self.holderAttribute = vgle_gpkgs.setHolderFieldGPKG(gpkg_path, tempLayerName, parameters["AssignedByField"])
        self.idAttribute = vgle_gpkgs.createIdFieldGPKG(gpkg_path, tempLayerName)
        holdersWithHoldings, holdersHoldingNumber = vgle_gpkgs.getHoldersHoldingsGPKG(gpkg_path, tempLayerName, self.holderAttribute, self.idAttribute)
        vgle_gpkgs.sortHolderWithHoldings(holdersWithHoldings)
        holdingsWithArea = vgle_gpkgs.getHoldingsAreasGPKG(gpkg_path, tempLayerName, self.weight, self.idAttribute)
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = vgle_utils.calculateTotalArea(self.holdersWithHoldings, self.holdingsWithArea)

        self.seeds =  vgle_gpkgs.determineSeedPolygonsGPKG(self, gpkg_path, tempLayerName, selectedHoldersRowIds)
        self.selectedHoldersIds = vgle_gpkgs.getSelectedHolders(self, gpkg_path, tempLayerName, self.parameterAsVectorLayer(parameters, 'Inputlayer', context))
        feedback.pushInfo(f'Bottom Up group members (lenght - {len(self.selectedHoldersIds)}): {self.selectedHoldersIds}')

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
            self.potentialInteractionTable = vgle_utils.createInteractionOutput(self.holdersWithHoldings)
            
            mergedBELayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
            _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedBELayer)
            mergedBETable = vgle_gpkgs.calculateStatDataMergedGPKG(self, gpkg_path, mergedBELayer, self.holderAttribute, timeStamp)
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
        self.changeLog = vgle_gpkgs.createExchangeLog(self)
        self.actualIdAttribute, self.actualHolderAttribute = copy.copy(self.idAttribute), copy.copy(self.holderAttribute)   
        oneSeedBoolean = vgle_gpkgs.checkSeedNumberGPKG(self, feedback)
        self.groupHolders = []
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
            if self.resultType == 1:
                vgle_gpkgs.createGroupFlag(gpkg_path, tempLayerName, self.actualHolderAttribute, self.selectedHoldersIds)
            else:
                vgle_gpkgs.deleteRowsByAttributeValues(gpkg_path, tempLayerName, self.actualHolderAttribute, [k for k in list(self.holdersWithHoldings.keys()) if k not in self.selectedHoldersIds])
                
            mergedLayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
            #wait(5000)
            vgle_gpkgs.copyFieldGPKG(gpkg_path, mergedLayer, self.actualHolderAttribute, parameters["AssignedByField"][0])
            vgle_gpkgs.copyFieldGPKG(gpkg_path, mergedLayer, self.actualHolderAttribute, parameters["AssignedByField"][0])
            toDeleteAttr = [attr for attr in vgle_gpkgs.getFieldNamesGPKG(gpkg_path, mergedLayer)
                            if attr not in vgle_layers.getAttributesNames(self.parameterAsVectorLayer(parameters, 'Inputlayer', context)) and attr not in [self.idAttribute, 'seed_flag', 'geom']]
            vgle_gpkgs.deleteField(gpkg_path, mergedLayer, toDeleteAttr)
            
            gpkg_path, layer_name = self.layer
            if parameters['Stats']:
                if self.resultType == 0:
                    afterHoldersWithHoldings, _ = vgle_gpkgs.getHoldersHoldingsGPKG(gpkg_path, layer_name, self.actualHolderAttribute, self.idAttribute)
                    self.holdersWithHoldings = afterHoldersWithHoldings
                exc_freq_table, changes = vgle_gpkgs.saveInteractionOutput1GPKG(self)
                swap_freq_table = vgle_gpkgs.saveInteractionOutput2GPKG(self) 
                potential_swap_table = vgle_gpkgs.saveInteractionOutput3GPKG(self)               
                vgle_gpkgs.calculateStatDataGPKG(self, gpkg_path, tempLayerName, indicatorTable, 'AE', self.actualHolderAttribute)
                _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedLayer)
                mergedAETable = vgle_gpkgs.calculateStatDataMergedGPKG(self, gpkg_path, mergedLayer, self.actualHolderAttribute, timeStamp)
                vgle_gpkgs.calculateIndexDataGPKG(gpkg_path, indicatorTable, 'AE', mergedAETable)
                vgle_gpkgs.calculateIndexDifferencesGPKG(self, gpkg_path, tempLayerName, indicatorTable, changes)
                #vgle_gpkgs.calculateIndexDataGPKG(gpkg_path, indicatorTable, 'CH', mergedAETable)
                vgle_gpkgs.deleteTable(gpkg_path, mergedAETable)

                results['SWAP_FREQ_TABLE'] = swap_freq_table

            if len(parameters["AssignedByField"]) == 1:
                vgle_gpkgs.copyFieldGPKG(gpkg_path, tempLayerName, self.actualHolderAttribute, parameters["AssignedByField"][0])

            vgle_gpkgs.deleteTable(gpkg_path, self.distanceMatrixTable)
            vgle_gpkgs.deleteIndexes(self.layer[0], self.layer[1])
            vgle_gpkgs.deleteIndexes(self.layer[0], mergedLayer)

            cleaned_name = layer_name.replace('"', '').replace("'", '').strip()
            uri = f'{gpkg_path}|layername={cleaned_name}'
            feedback.pushInfo(f"URI: {uri}")
            context.addLayerToLoadOnCompletion(
                uri,
                QgsProcessingContext.LayerDetails(layer_name, context.project())
            )

            cleaned_name = mergedLayer.replace('"', '').replace("'", '').strip()
            uri = f'{gpkg_path}|layername={cleaned_name}'
            feedback.pushInfo(f"URI: {uri}")
            context.addLayerToLoadOnCompletion(
                uri,
                QgsProcessingContext.LayerDetails(mergedLayer, context.project())
            )

            if parameters['Stats']:
                cleaned_name = swap_freq_table.replace('"', '').replace("'", '').strip()
                uri = f'{gpkg_path}|layername={cleaned_name}'
                feedback.pushInfo(f"URI: {uri}")
                context.addLayerToLoadOnCompletion(
                    uri,
                    QgsProcessingContext.LayerDetails(swap_freq_table, context.project())
                )

                cleaned_name = exc_freq_table.replace('"', '').replace("'", '').strip()
                uri = f'{gpkg_path}|layername={cleaned_name}'
                feedback.pushInfo(f"URI: {uri}")
                context.addLayerToLoadOnCompletion(
                    uri,
                    QgsProcessingContext.LayerDetails(exc_freq_table, context.project())
                )

                cleaned_name = potential_swap_table.replace('"', '').replace("'", '').strip()
                uri = f'{gpkg_path}|layername={cleaned_name}'
                feedback.pushInfo(f"URI: {uri}")
                context.addLayerToLoadOnCompletion(
                    uri,
                    QgsProcessingContext.LayerDetails(potential_swap_table, context.project())
                )

            mainEndTime = time.time()
            logging.debug(f'Script time:{mainEndTime-mainStartTime}')

            feedback.setCurrentStep(self.steps)
            vgle_utils.endLogging()   
            results['OUTPUT'] = f'{gpkg_path}|layername={tempLayerName}'
            results['CLOG'] =  f'{gpkg_path}|layername={self.changeLog}'
            results['MERGED'] = f'{gpkg_path}|layername={mergedLayer}'
            return results
        else:
            swappedLayer = f'{gpkg_path}|layername={tempLayerName}'
            mergedLayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
            mergedLayer = f'{gpkg_path}|layername={mergedLayer}'

            vgle_gpkgs.deleteTable(gpkg_path, self.distanceMatrixTable)
            vgle_gpkgs.deleteIndexes(self.layer[0], self.layer[1])
            vgle_gpkgs.deleteIndexes(self.layer[0], mergedLayer)

            if self.strictHDI or self.strictHFI:
                feedback.pushInfo('No change was made, probably due to the too strict conditions (HDI or HFI)! Try to disable these parameters and run again.') 
            elif self.counter == 0:
                feedback.pushInfo('No change was made! Try to modify the parameters and run again.') 
            else:
                feedback.reportError('Something went wrong, no change was made! See log for more details.')
            vgle_utils.endLogging()
            results['OUTPUT'] = swappedLayer
            results['MERGED'] = mergedLayer
            return results 
