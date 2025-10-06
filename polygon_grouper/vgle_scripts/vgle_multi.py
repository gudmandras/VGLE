import os
import re
import platform
import math
import sys
from multiprocessing import Pool

from qgis.core import (QgsProject,
                       QgsApplication,
                       QgsVectorLayer,
                       QgsFeatureRequest,
                       QgsVectorFileWriter)

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
    print(chunkSize)

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

def neighbour_multi():
    pass

def closer_multi():
    pass

def hybrid_multi():
    pass


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
