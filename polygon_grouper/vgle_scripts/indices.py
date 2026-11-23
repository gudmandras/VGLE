from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (QgsProject,
                       QgsApplication,
                       QgsProcessing,
                       QgsProcessingAlgorithm,
                       QgsProcessingContext,
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
from . import vgle_utils, vgle_layers, vgle_features, vgle_gpkgs



class StatAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterField('BEByField', 'Before holder by field',
                                                      type=QgsProcessingParameterField.Any,
                                                      parentLayerParameterName='Inputlayer'))
        self.addParameter(QgsProcessingParameterField('AEByField', 'After holder by field',
                                                      type=QgsProcessingParameterField.Any,
                                                      parentLayerParameterName='Inputlayer'))
        self.addParameter(QgsProcessingParameterField('BalancedByField', 'Area by field',
                                                      type=QgsProcessingParameterField.Numeric,
                                                      parentLayerParameterName='Inputlayer',
                                                      allowMultiple=False, defaultValue=''))
        self.onlySelected = False
        self.useSingle = False
        self.backup_data = {}
        

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
        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        results = {}
        filePath = self.parameterAsVectorLayer(parameters, 'Inputlayer', context).source()
        self.weight = parameters['BalancedByField']      
        if filePath[-4:].lower() != 'gpkg' and os.path.splitext(filePath)[1][:5].lower() != '.gpkg':
            feedback.reportError('The layer is not part of a GPKG')
            return {}
        
        gpkg_path, tempLayerName = vgle_gpkgs.createTempLayerIntoGPKG(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), 'forIndices', timeStamp, feedback)
        if not gpkg_path or not tempLayerName:
            feedback.reportError('Failed to create temporary layer in GPKG. Check the log for more details.')
            vgle_utils.endLogging()
            return {}
        
        self.holderAttribute = vgle_gpkgs.setHolderFieldGPKG(gpkg_path, tempLayerName, [parameters["BEByField"]])
        self.idAttribute = vgle_gpkgs.createIdFieldGPKG(gpkg_path, tempLayerName)
        holdersWithHoldings, holdersHoldingNumber = vgle_gpkgs.getHoldersHoldingsGPKG(gpkg_path, tempLayerName, self.holderAttribute, self.idAttribute)
        vgle_gpkgs.sortHolderWithHoldings(holdersWithHoldings)
        holdingsWithArea = vgle_gpkgs.getHoldingsAreasGPKG(gpkg_path, tempLayerName, self.weight, self.idAttribute)
        self.holdersWithHoldings = holdersWithHoldings
        self.holdersHoldingNumber = holdersHoldingNumber
        self.holdingsWithArea = holdingsWithArea
        self.holdersTotalArea = vgle_utils.calculateTotalArea(self.holdersWithHoldings, self.holdingsWithArea)

        selectedHoldersRowIds = None
        self.seeds =  vgle_gpkgs.determineSeedPolygonsGPKG(self, gpkg_path, tempLayerName, selectedHoldersRowIds)

        feedback.pushInfo('Calculate distance matrix')
        featureThreshold = 5000
        totalFeatures = vgle_gpkgs.getFeatureCountGPKG(gpkg_path, tempLayerName)
        if totalFeatures > featureThreshold:
            distanceMatrix = vgle_gpkgs.createDistanceMatrixGPKG(self, gpkg_path, tempLayerName, context, feedback, nearestPoints=int(totalFeatures*0.1), simply=True)
        else:
            distanceMatrix = vgle_gpkgs.createDistanceMatrixGPKG(self, gpkg_path, tempLayerName, context, feedback)
        self.distanceMatrix = distanceMatrix
        self.distanceMatrixTable = vgle_gpkgs.saveDistanceMatrix(gpkg_path, tempLayerName, distanceMatrix)
        feedback.pushInfo('Distance matrix calculated')

        feedback.pushInfo('Calculate total distances')
        originalTotalDistance, self.totalDistance = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, tempLayerName)
        feedback.pushInfo('Total distances calculated')

        uri = self.parameterAsVectorLayer(parameters, 'Inputlayer', context).dataProvider().dataSourceUri()
        _, source_layer_name = uri.split("|layername=")
        statTable = vgle_gpkgs.createStatTableForIndicesGPKG(self, gpkg_path, source_layer_name)

        be_data = vgle_gpkgs.calculateStatDataForIndicesFileGPKG(self, gpkg_path, tempLayerName, parameters["BEByField"])
        mergedBELayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
        _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedBELayer)
        be_merged_data = vgle_gpkgs.calculateStatDataMergedForIndicesGPKG(self, gpkg_path, mergedBELayer, parameters["BEByField"])
        merged_dict = {
            row[0]: row[1:]
            for row in be_merged_data
        }
        be_data = [
            (row[0], merged_dict[row[0]][0], row[2], merged_dict[row[0]][1])
            if row[0] in merged_dict else row
            for row in be_data
        ]
        vgle_gpkgs.deleteTable(gpkg_path, mergedBELayer)

        ae_data = vgle_gpkgs.calculateStatDataForIndicesFileGPKG(self, gpkg_path, tempLayerName, parameters["AEByField"])
        mergedBELayer = vgle_gpkgs.createMergedFileGPKG(self, gpkg_path, tempLayerName, context, feedback)
        _, __ = vgle_gpkgs.calculateTotalDistancesGPKG(self, gpkg_path, mergedBELayer)
        ae_merged_data = vgle_gpkgs.calculateStatDataMergedForIndicesGPKG(self, gpkg_path, mergedBELayer, parameters["AEByField"])
        merged_dict = {
            row[0]: row[1:]
            for row in ae_merged_data
        }
        ae_data = [
            (row[0], merged_dict[row[0]][0], row[2], merged_dict[row[0]][1])
            if row[0] in merged_dict else row
            for row in ae_data
        ]
        vgle_gpkgs.deleteTable(gpkg_path, mergedBELayer)

        vgle_gpkgs.populateFragmentationIndicatorsTableGPKG(gpkg_path, statTable, be_data, ae_data)

        vgle_gpkgs.deleteTable(gpkg_path, tempLayerName)
        vgle_gpkgs.deleteTable(gpkg_path, self.distanceMatrixTable)

        results['frag_stat'] = statTable
        
        uri = f'{gpkg_path}|layername={statTable}'
        feedback.pushInfo(f"URI: {uri}")
        context.addLayerToLoadOnCompletion(
            uri,
            QgsProcessingContext.LayerDetails(statTable, context.project())
        )

        return results

                


            




        

        

        
