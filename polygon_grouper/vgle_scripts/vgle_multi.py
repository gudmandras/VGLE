import os
import re
import platform
import math
import sys
import traceback
from multiprocessing import Pool

from qgis.core import (QgsTask,
                       QgsProject,
                       QgsApplication,
                       QgsVectorLayer,
                       QgsVectorFileWriter)

from . import vgle_utils, vgle_features, vgle_methods, vgle_layers

def import_processing(prefix):
    QgsApplication.setPrefixPath(prefix, True)
    qgs = QgsApplication([], False)
    qgs.initQgis()

    import processing
    from processing.core.Processing import Processing
    Processing.initialize()

def import_vgle():
    plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    from vgle_scripts import vgle_layers

def multi_distance_matrix(outputDirectory, centroids, idAttribute):
    layer = QgsVectorLayer(centroids, f"centroids", "ogr")
    cpus = os.cpu_count() - 2
    totalFeatures = layer.featureCount()
    chunkSize = math.ceil(totalFeatures / cpus)
    chunkFiles = split_centroids_to_chunks(layer, outputDirectory, chunkSize)

    tasks = []
    for taskNum in range(0, totalFeatures, chunkSize):
        tasks.append((chunkFiles[taskNum], centroids, idAttribute))

    with Pool(cpus) as pool:
        results = pool.starmap(distance_matrix_process, tasks)

    distanceMatrix = {}
    for part in results:
        distanceMatrix.update(part)

    outJson = os.path.join(outputDirectory, "distance_matrix.json")
    with open(outJson, "w", encoding="utf-8") as f:
        json.dump(distanceMatrix, f, ensure_ascii=False, indent=2)

def split_centroids_to_chunks(layer, outputDirectory, chunkSize):
    total = layer.featureCount()
    feature_ids = [f.id() for f in layer.getFeatures()]

    chunkFiles = []
    for i in range(0, total, chunkSize):
        chunk_ids = feature_ids[i:i + chunkSize]
        layer.selectByIds(chunk_ids)

        chunkFile = os.path.join(outputDirectory, f"centroids_chunk_{i}.gpkg")
        
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.fileEncoding = "UTF-8"
        options.onlySelected = True
        context = QgsProject.instance().transformContext()

        QgsVectorFileWriter.writeAsVectorFormatV2(
            layer,
            chunkFile,
            context, 
            options
        )

        chunkFiles.append(chunkFile)
        layer.removeSelection()

    return chunkFiles

def distance_matrix_process(input_centroids ,centroids, idAttribute):
    algParams = {
        'INPUT': input_centroids,
        'INPUT_FIELD': idAttribute,
        'TARGET': centroids,
        'TARGET_FIELD': idAttribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    matrix = processing.run("qgis:distancematrix", algParams)['OUTPUT']

    distanceMatrix = {}
    names = vgle_layers.getAttributesNames(matrix)
    features = matrix.getFeatures()
    for feature in features:
        tempDict = {}
        for field in names:
            value = feature.attribute(field)
            tempDict[field] = value
        distanceMatrix[feature.attribute('ID')] = tempDict

    return matrix

class NeighbourFunctionComparisonTask(QgsTask):

    def __init__(self, name, holder, targetHolder, holderCombination, targetCombination, holderSeed, targetSeed, holderTotalArea, targetTotalArea, context, strict=False, useSingle=False, on_finished=None):
        super().__init__(name, QgsTask.CanCancel)
        self.holder = holder
        self.targetHolder = targetHolder
        self.holderCombination = holderCombination
        self.targetCombination = targetCombination
        self.holderSeed = holderSeed
        self.targetSeed = targetSeed
        self.holderTotalArea = holderTotalArea
        self.targetTotalArea = targetTotalArea
        self.context = context
        self.strict = strict
        self.useSingle = useSingle
        self.on_finished = on_finished
        self.result = None
        self.difference = None
        self.exception = None
        #for attr, value in self.__dict__.items():
        #    print(f"Attribute '{attr}': {repr(value)}\n")

    def run(self):
        """Here you implement your heavy lifting.
        Should periodically test for isCanceled() to gracefully
        abort.
        This method MUST return True or False.
        Raising exceptions will crash QGIS, so we handle them
        internally and raise them in self.finished
        """
        try:
            temporaryHolderArea = vgle_utils.calculateCombinationArea(self.context, self.holderCombination)                             
            if self.strict:
                # Distance conditions
                holderMaxDistance = vgle_features.maxDistance(self.context, self.holderCombination, self.holderSeed)
                holderAvgDistanceOld = vgle_features.avgDistance(self.context, self.holderCombination, self.holderSeed)
                holderAvgDistanceNew = vgle_features.avgDistance(self.context, self.targetCombination, self.holderSeed)
                if self.useSingle and not self.targetSeed :
                    targetCloser = True
                    targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                    targetMaxDistance = vgle_features.maxDistance(self.context, self.targetCombination, self.targetCombination[0])
                    holderCloser = vgle_utils.isCloser(self.context, targetMaxDistance, self.holderCombination, self.targetSeed, self.targetHolder)
                else:
                    targetMaxDistance = vgle_features.maxDistance(self.context, self.targetCombination, self.targetSeed)
                    targetAvgDistanceOld = vgle_features.avgDistance(self.context, self.targetCombination, self.targetSeed)
                    targetAvgDistanceNew = vgle_features.avgDistance(self.context, self.holderCombination, self.targetSeed)
                    targetCloser = vgle_utils.isCloser(self.context, holderMaxDistance, self.targetCombination, self.holderSeed, self.holder)
                    holderCloser = vgle_utils.isCloser(self.context, targetMaxDistance, self.holderCombination, self.targetSeed, self.targetHolder)
            else:
                targetCloser, holderCloser = True, True
                targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                holderAvgDistanceNew, holderAvgDistanceOld = 1,2

            if self.isCanceled():
                return False
            
            if targetCloser and holderCloser:
                if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                    #Weight condition
                    temporaryTargetArea = vgle_utils.calculateCombinationArea(self.context, self.targetCombination)
                    newHolderTotalArea = self.holderTotalArea - temporaryHolderArea + temporaryTargetArea
                    newNeighbourTotalArea = self.targetTotalArea - temporaryTargetArea + temporaryHolderArea
                    thresholdHolder = vgle_utils.checkTotalAreaThreshold(self.context, newHolderTotalArea, self.holder)
                    thresholdNeighbour = vgle_utils.checkTotalAreaThreshold(self.context, newNeighbourTotalArea, self.targetHolder )
                    difference = abs(newHolderTotalArea-self.holderTotalArea)
                    print(difference)
                    if thresholdHolder and thresholdNeighbour:
                        holderNewHoldignNum = self.context.holdersHoldingNumber[self.holder] - len(self.holderCombination) + len(self.targetCombination)
                        targetNewHoldingNum = self.context.holdersHoldingNumber[self.targetHolder ] - len(self.targetCombination) + len( self.holderCombination)
                        if self.strict:
                            if holderNewHoldignNum > self.context.holdersHoldingNumber[self.holder] and targetNewHoldingNum > self.context.holdersHoldingNumber[self.targetHolder]:
                                return False
                        self.result = (self.holderCombination, self.targetCombination, newHolderTotalArea, newNeighbourTotalArea, difference)
                        return True
                    else:
                        return False
        except Exception as e:
            self.exception = traceback.format_exc()
            return False

    def finished(self, result):
        if self.on_finished:
            if result is True:
                self.on_finished(True, self.result)
            else:
                if self.exception:
                    self.on_finished(False, self.exception)
                else:
                    self.on_finished(False, None)


class CloserFunctionComparisonTask(QgsTask):

    def __init__(self, name, holder, targetHolder, holderCombination, targetCombination, holderSeed, targetSeed, holderTotalArea, targetTotalArea, context, strict=False, useSingle=False, on_finished=None):
        super().__init__(name, QgsTask.CanCancel)
        self.holder = holder
        self.targetHolder = targetHolder
        self.holderCombination = holderCombination
        self.targetCombination = targetCombination
        self.holderSeed = holderSeed
        self.targetSeed = targetSeed
        self.holderTotalArea = holderTotalArea
        self.targetTotalArea = targetTotalArea
        self.context = context
        self.strict = strict
        self.useSingle = useSingle
        self.on_finished = on_finished
        self.result = None
        self.difference = None
        self.exception = None
        #for attr, value in self.__dict__.items():
        #    print(f"Attribute '{attr}': {repr(value)}\n")

    def run(self):
        """Here you implement your heavy lifting.
        Should periodically test for isCanceled() to gracefully
        abort.
        This method MUST return True or False.
        Raising exceptions will crash QGIS, so we handle them
        internally and raise them in self.finished
        """
        try:
            holderMaxDistance = vgle_features.maxDistance(self.context, self.holderCombination, self.holderSeed)
            targetMaxDistance = vgle_features.maxDistance(self.context, self.targetCombination, self.targetSeed)
            targetCloser = vgle_utils.isCloser(self.context, holderMaxDistance, self.targetCombination, self.holderSeed, self.holder)
            holderCloser = vgle_utils.isCloser(self.context, targetMaxDistance, self.holderCombination, self.targetSeed, self.targetHolder)
            
            if not targetCloser or not holderCloser:
                return False
            
            holderAvgDistanceOld = vgle_features.avgDistance(self.context, self.holderCombination, self.holderSeed)
            holderAvgDistanceNew = vgle_features.avgDistance(self.context, self.targetCombination, self.holderSeed)
            targetAvgDistanceOld = vgle_features.avgDistance(self.context, self.targetCombination, self.targetSeed)
            targetAvgDistanceNew = vgle_features.avgDistance(self.context, self.holderCombination, self.targetSeed)

            if (targetAvgDistanceNew >= targetAvgDistanceOld) or (holderAvgDistanceNew >= holderAvgDistanceOld):
                return False

            if self.isCanceled():
                return False

            newHolderTotalArea = self.holderTotalArea - vgle_utils.calculateCombinationArea(self.context, self.holderCombination) + vgle_utils.calculateCombinationArea(self.context,  self.targetCombination)
            if not vgle_utils.checkTotalAreaThreshold(self.context, newHolderTotalArea, self.holder):
                return False
            newTargetTotalArea =  self.targetTotalArea - vgle_utils.calculateCombinationArea(self.context, self.targetCombination) + vgle_utils.calculateCombinationArea(self.context, self.holderCombination)
            if not vgle_utils.checkTotalAreaThreshold(self.context, newTargetTotalArea, self.targetHolder):
                return False
            localMeasure = sum([vgle_utils.calculateCompositeNumber(self.context, self.holderSeed, tempId) for tempId in self.holderCombination])
            holderNewHoldingNum = self.context.holdersHoldingNumber[self.holder] - len(self.holderCombination) + len(self.targetCombination)
            targetNewHoldingNum = self.context.holdersHoldingNumber[self.targetHolder] - len(self.targetCombination) + len(self.holderCombination)
            if holderNewHoldingNum <= self.context.holdersHoldingNumber[self.holder] and targetNewHoldingNum <= self.context.holdersHoldingNumber[self.targetHolder]:
                self.result = (self.holderCombination, self.targetCombination, newHolderTotalArea, newTargetTotalArea, localMeasure)
                return True
            else:
                return False
        except Exception as e:
            self.exception = traceback.format_exc()
            return False

    def finished(self, result):
        if self.on_finished:
            if result is True:
                self.on_finished(True, self.result)
            else:
                if self.exception:
                    self.on_finished(False, self.exception)
                else:
                    self.on_finished(False, None)

if __name__ == "__main__":
    try:
        prefix = sys.argv[2]
        import_processing(prefix)
        import_vgle()

        if 'distanceMatrix' in sys.argv[1:]:
            outputDirectory = sys.argv[3]
            centroids = sys.argv[4]
            idAttribute = sys.argv[5]
            multi_distance_matrix(outputDirectory, centroids, idAttribute)
    except Exception as e:
        print(e)
