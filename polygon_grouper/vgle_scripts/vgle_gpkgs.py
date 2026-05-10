import os
import uuid
import copy
import qgis
import processing
import random
import logging
import sqlite3
import re
import math
import time
from collections import defaultdict

from qgis.PyQt.QtCore import QVariant
from PyQt5.QtCore import QCoreApplication
from qgis.core import (QgsVectorFileWriter,
                       QgsVectorLayer,
                       QgsProject,
                       QgsProcessingUtils,
                       QgsField,
                       QgsFeatureRequest,
                       QgsSpatialIndex,
                       QgsProcessingOutputLayerDefinition)
from . import vgle_layers, vgle_utils


def checkTableName(parameters, feedback):
    allowed_pattern = re.compile(r"^[A-Za-z0-9_]+$")

    if not allowed_pattern.match(parameters['BalancedByField']):
            feedback.reportError(f"Invalid field name: {parameters['BalancedByField']}")
            return False

    for field in parameters['AssignedByField']:
        if not allowed_pattern.match(field):
            feedback.reportError(f"Invalid field name: {field}")
            return False

    return True

def checkSwapFreq(swap_layer):
    required_fields = {"from", "to", "weight"}
    uri = swap_layer.dataProvider().dataSourceUri()
    try:
        gpkg_path, source_layer_name = uri.split("|layername=")
    except ValueError:
        gpkg_path = uri

    if gpkg_path[-4:].lower() != 'gpkg' and os.path.splitext(gpkg_path)[1][:5].lower() != '.gpkg':
        return False

    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f'PRAGMA table_info("{source_layer_name}")')
    columns = cur.fetchall()

    conn.close()

    for field in required_fields:
        if field not in [col[1] for col in columns]:
            return False

    return uri, source_layer_name

def connectToDB(gpkg_path):
    conn = sqlite3.connect(gpkg_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = 1000000;")
    return conn

def createTempLayerIntoGPKG(layer, postfix, timeStamp, feedback):
    #import ptvsd
    #ptvsd.debug_this_thread()
    uri = layer.dataProvider().dataSourceUri()
    try:
        gpkg_path, source_layer_name = uri.split("|layername=")
    except ValueError:
        gpkg_path = uri
        source_layer_name = getFirstLayerFromGPKG(gpkg_path)
        if not source_layer_name:
            feedback.reportError("No feature layers found in the GPKG.")
            return None, None

    target_layer_name = f"{str(source_layer_name.replace(' ', '_'))}_{postfix}_{timeStamp}"
    feedback.pushInfo(source_layer_name)

    gpkg_path = os.path.normpath(gpkg_path)

    feedback.pushInfo(gpkg_path)

    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (source_layer_name,))

    row = cur.fetchone()
    create_sql = row[0]
    feedback.pushInfo(create_sql)
    
    new_create_sql = re.sub(
    r'CREATE TABLE\s+"?{}"?'.format(re.escape(source_layer_name)),
    f'CREATE TABLE "{target_layer_name}"',
    create_sql,
    count=1,
    flags=re.IGNORECASE
    )

    cur.execute(new_create_sql)
    cur.execute(f'INSERT INTO "{target_layer_name}" SELECT * FROM "{source_layer_name}"')
    cur.execute(
        """
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description,
            last_change, min_x, min_y, max_x, max_y, srs_id
        )
        SELECT ?, data_type, ?, description,
            CURRENT_TIMESTAMP, min_x, min_y, max_x, max_y, srs_id
        FROM gpkg_contents
        WHERE table_name=?
        """,
        (target_layer_name, target_layer_name, source_layer_name)
    )

    cur.execute(
        """
        INSERT INTO gpkg_geometry_columns (
            table_name, column_name, geometry_type_name,
            srs_id, z, m
        )
        SELECT ?, column_name, geometry_type_name, srs_id, z, m
        FROM gpkg_geometry_columns
        WHERE table_name=?
        """,
        (target_layer_name, source_layer_name)
    )

    conn.commit()
    conn.close()

    return gpkg_path, target_layer_name

def getFirstLayerFromGPKG(gpkg_path):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name
        FROM gpkg_contents
        WHERE data_type = 'features'
        ORDER BY table_name
        LIMIT 1
    """)

    result = cur.fetchone()
    conn.close()

    if result:
        layer_name = result[0]
        return layer_name
    else:
        return None

def setHolderFieldGPKG(gpkg_path, layer_name, attributes, holder_field='holder_id'):
    if len(attributes) > 1:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
        columns = [row[1] for row in cur.fetchall()]
        
        original_holder_field = holder_field
        counter = 0
        while holder_field in columns:
            holder_field = f"{original_holder_field}_{counter}"
            counter += 1

        cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN "{holder_field}" INTEGER')

        attr_str = ', '.join(f'"{attr}"' for attr in attributes)
        unique_combinations_table = f"{layer_name}_unique_combinations"

        cur.execute(f'DROP TABLE IF EXISTS "{unique_combinations_table}"')
        sql_unique = f"""CREATE TEMP TABLE "{unique_combinations_table}" AS
                        SELECT MIN(rowid) AS rowid, {attr_str},
                        CAST(ROW_NUMBER() OVER () AS INTEGER) AS holder_val
                        FROM "{layer_name}"
                        GROUP BY {attr_str}
                        """
        cur.execute(sql_unique)

        set_expr = f'UPDATE "{layer_name}" SET "{holder_field}" = (' \
                f'SELECT holder_val FROM "{unique_combinations_table}" u ' \
                f'WHERE ' + ' AND '.join([f'"{layer_name}"."{attr}" = u."{attr}"' for attr in attributes]) + \
                f')'
        cur.execute(set_expr)

        cur.execute(f'''
            CREATE INDEX IF NOT EXISTS idx_{layer_name}_{holder_field}
            ON "{layer_name}" ("{holder_field}")
        ''')

        conn.commit()
        conn.close()

        return holder_field
    else:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()

        print(f'SQL: CREATE INDEX IF NOT EXISTS idx_{layer_name}_{attributes[0]} ON "{layer_name}" ("{attributes[0]}"')

        cur.execute(f'''
            CREATE INDEX IF NOT EXISTS "idx_{layer_name}_{attributes[0]}"
            ON "{layer_name}" ("{attributes[0]}")
        ''')

        conn.commit()
        conn.close()

        return attributes[0]

def createIdFieldGPKG(gpkg_path, layer_name):
    new_field = 'polygon_id'
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"PRAGMA table_info('{layer_name}')")
    columns = [col[1] for col in cur.fetchall()]
    original_field = new_field
    counter = 0
    while new_field in columns:
        new_field = f"{original_field}_{counter}"
        counter += 1
    cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN "{new_field}" TEXT')
    
    cur.execute(f'SELECT rowid FROM "{layer_name}" ORDER BY rowid')
    rowids = [row[0] for row in cur.fetchall()]
    uuid_list = [str(uuid.uuid4())[:10] for _ in rowids]

    cur.executemany(
        f'UPDATE "{layer_name}" SET "{new_field}"=? WHERE rowid=?',
        zip(uuid_list, rowids)
    )

    cur.execute(f'''
        CREATE INDEX IF NOT EXISTS idx_{layer_name}_{new_field}
        ON "{layer_name}" ("{new_field}")
    ''')

    conn.commit()
    conn.close()

    return new_field

def copyFieldGPKG(gpkg_path, layer_name, source_field, target_field): 
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    try:
        cur.execute(f'''
            UPDATE "{layer_name}"
            SET {target_field} = {source_field}
        ''')
    except sqlite3.OperationalError:
        dropTriggers(gpkg_path, layer_name)
        try:
            cur.execute(f'''
                UPDATE "{layer_name}"
                SET {target_field} = {source_field}
            ''')
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def saveDistanceMatrix(gpkg_path, layer_name, matrix):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    target_layer_name = f"{str(layer_name)}_distance_matrix"
    
    cur.execute(f'DROP TABLE IF EXISTS "{target_layer_name}"')

    # Create table
    cur.execute(f"""
        CREATE TABLE "{target_layer_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_id TEXT,
            target_id TEXT,
            distance REAL
        )
    """)

    # Optional: register as GPKG layer (non-spatial)
    cur.execute(f"""
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description
        )
        VALUES (?, 'attributes', ?, 'Distance matrix table')
    """, (target_layer_name, target_layer_name))

    # Bulk insert
    rows = [
        (str(input_id), str(target_id), float(distance))
        for input_id, targets in matrix.items()
        for target_id, distance in targets.items()
    ]

    cur.executemany(
        f'INSERT INTO "{target_layer_name}" (input_id, target_id, distance) VALUES (?, ?, ?)',
        rows
    )

    cur.execute(f'''
        CREATE INDEX IF NOT EXISTS idx_{layer_name}_input_id
        ON "{layer_name}" ("input_id")
    ''')

    cur.execute(f'''
        CREATE INDEX IF NOT EXISTS idx_{layer_name}_target_id
        ON "{layer_name}" ("target_id")
    ''')

    cur.execute(f'''
        CREATE INDEX IF NOT EXISTS idx_{layer_name}_distance
        ON "{layer_name}" ("distance")
    ''')

    conn.commit()
    conn.close()

    return target_layer_name

def filterDistanceMatrix(gpkg_path, distance_matrix, distance):
    filteredMatrix = {}
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""SELECT input_id, target_id, distance FROM "{distance_matrix}" WHERE distance <= ?""", (distance,))
    
    for source, target, dist in cur.fetchall():
        if source not in filteredMatrix:
            filteredMatrix[source] = {}
        filteredMatrix[source][target] = dist
    return filteredMatrix

def createStatTableGPKG(self, gpkg_path, layer_name):
    target_layer_name = f'{layer_name}_indicators'
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{target_layer_name}"')
    cur.execute(f"""
        CREATE TABLE "{target_layer_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            "Holder ID" TEXT,
            "BE # of polygons (HFI)" INTEGER,
            "BE Sum balancing value (PFI)" REAL,
            "BE Distance (m) (HDI)" REAL,
            "BE HFI" REAL,
            "BE PFI" REAL,
            "BE HDI" REAL,
            "AE # of polygons (HFI)" INTEGER,
            "AE Sum balancing value (PFI)" REAL,
            "AE Distance (m) (HDI)" REAL,
            "CH # of polygons (HFI)" REAL,
            "CH Sum balancing value (PFI)" REAL,
            "CH Distance (m) (HDI)" REAL,
            "AE HFI" REAL,
            "AE PFI" REAL,
            "AE HDI" REAL,
            "CH HFI" REAL,
            "CH PFI" REAL,
            "CH HDI" REAL,
            "Change num" INTEGER
        )
    """)

    cur.execute(f"""
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description
        )
        VALUES (?, 'attributes', ?, 'Indicator table')
    """, (target_layer_name, target_layer_name))

    cur.execute(f'''
        INSERT INTO "{target_layer_name}" ("Holder ID")
        SELECT DISTINCT "{self.holderAttribute}"
        FROM "{layer_name}"
        WHERE "{self.holderAttribute}" IS NOT NULL
    ''')

    conn.commit()
    conn.close()

    return target_layer_name

def createMergedFileGPKG(self, gpkg_path, layer_name, context, feedback):
    """
    DESCRIPTION: Create merged file based on holders attribute field
    INPUTS:
            layer: QgsVectorLayer
            directory: String, absolute path to save the new layer
    OUTPUTS: None
    """
    attributeName = self.holderAttribute if not hasattr(self, 'actualHolderAttribute') else self.actualHolderAttribute

    uri = f"{gpkg_path}|layername={layer_name}"
    layer = QgsVectorLayer(uri, layer_name, "ogr")

    algParams = {
        'EXPRESSION': f'array_contains (overlay_touches (@layer, \"{attributeName}\", limit:=-1), \"{attributeName}\")',
        'INPUT': layer,
        'METHOD': 0
    }
    processing.run('qgis:selectbyexpression', algParams, context=context, feedback=feedback, is_child_algorithm=True)

    # Extract selected features
    algParams = {
        'INPUT': layer,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    selectedFeatures_temp = processing.run('native:saveselectedfeatures', algParams, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']
    selectedFeatures = QgsProcessingUtils.mapLayerFromString(selectedFeatures_temp, context)

    # Dissolve
    algParams = {
        'FIELD': [attributeName],
        'INPUT': selectedFeatures,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    dissolvedLayer_temp = processing.run('native:dissolve', algParams, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']
    dissolvedLayer = QgsProcessingUtils.mapLayerFromString(dissolvedLayer_temp, context)

    # Difference
    algParams = {
        'INPUT': layer,
        'OVERLAY': dissolvedLayer,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    differences_temp = processing.run('native:difference', algParams,context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']
    differences = QgsProcessingUtils.mapLayerFromString(differences_temp, context)

    # Merge vector layers
    crs = layer.crs()
    output_layer_name = f"{layer_name}_merged"
    newUri = f"ogr:dbname=\'{gpkg_path}\' table=\'{output_layer_name}\' (geom)"   

    algParams = {
        'CRS': crs,
        'LAYERS': [differences, dissolvedLayer],
        'OUTPUT': newUri
    }
    mergedLayer_temp = processing.run('native:mergevectorlayers', algParams, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']
    #mergedLayer = QgsProcessingUtils.mapLayerFromString(mergedLayer_temp, context)
    #mergedLayer.setName(f'{os.path.basename(layer.source())[:-4]}_merged')
    #mergedLayer.setCrs(layer.crs())
    #index = vgle_layers.getAttributesNames(mergedLayer).index(self.weight)
    #mergedLayer.startEditing()
    #for feature in mergedLayer.getFeatures():
    #    newValue = feature.geometry().area()
    #    mergedLayer.changeAttributeValue(feature.id(), index, newValue)
    #mergedLayer.commitChanges()

    #output_layer_name = f"{layer_name}_merged"

    #conn = sqlite3.connect(gpkg_path)
    #cur = conn.cursor()
    #cur.execute(f'DROP TABLE IF EXISTS "{output_layer_name}"')
    #cur.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (output_layer_name,))
    #conn.commit()
    #conn.close()


    #options = QgsVectorFileWriter.SaveVectorOptions()
    #options.driverName = "GPKG"
    #options.layerName = output_layer_name
    #options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

    #result, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
    #    mergedLayer,
    #    gpkg_path,
    #    context.transformContext(),
    #    options
    #)
    #if result == QgsVectorFileWriter.NoError:
    #    print("Merged file write completed successfully")

    #layer = QgsVectorLayer(newUri, output_layer_name, "ogr")
    #feedback.pushInfo(f"Output layer created: {output_layer_name} - feature count: {layer.featureCount()}")
    layer = None
    
    del dissolvedLayer, differences, mergedLayer_temp, layer
    #del mergedLayer, dissolvedLayer, differences, options, result

    return output_layer_name

def deleteTable(gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()
    # Remove spatial index first
    try:
        cur.execute(f"""
            SELECT DisableSpatialIndex('{layer_name}', 'geom')
        """)
    except:
        pass

    # Drop RTree tables
    cur.execute(f'''
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name LIKE 'rtree_{layer_name}%'
    ''')

    for (tbl,) in cur.fetchall():
        cur.execute(f'DROP TABLE IF EXISTS "{tbl}"')

    # Drop triggers
    cur.execute(f'''
        SELECT name
        FROM sqlite_master
        WHERE type='trigger'
        AND tbl_name=?
    ''', (layer_name,))

    for (trg,) in cur.fetchall():
        cur.execute(f'DROP TRIGGER IF EXISTS "{trg}"')

    # Drop indexes
    cur.execute(f'''
        SELECT name
        FROM sqlite_master
        WHERE type='index'
        AND tbl_name=?
    ''', (layer_name,))

    for (idx,) in cur.fetchall():
        if not idx.startswith("sqlite_autoindex"):
            cur.execute(f'DROP INDEX IF EXISTS "{idx}"')

    # Remove metadata
    cur.execute(
        "DELETE FROM gpkg_geometry_columns WHERE table_name=?",
        (layer_name,)
    )

    cur.execute(
        "DELETE FROM gpkg_contents WHERE table_name=?",
        (layer_name,)
    )

    cur.execute(
        "DELETE FROM gpkg_extensions WHERE table_name=?",
        (layer_name,)
    )

    # Finally drop actual table
    cur.execute(f'DROP TABLE IF EXISTS "{layer_name}"')

    conn.commit()
    conn.close()
    
    #cur = conn.cursor()
    #cur.execute(f'DROP TABLE IF EXISTS "{layer_name}"')
    #cur.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (layer_name,))
    #conn.commit()
    #conn.close()

def deleteField(gpkg_path, layer_name, field_names):
    conn = sqlite3.connect(gpkg_path)
    conn.enable_load_extension(True)
    conn.load_extension("mod_spatialite")
    
    cur = conn.cursor()

    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='trigger' AND tbl_name=?
    """, (layer_name,))

    for (trg,) in cur.fetchall():
        cur.execute(f'DROP TRIGGER IF EXISTS "{trg}"')

    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE name LIKE ?
    """, (f'rtree_{layer_name}_%',))

    for (t,) in cur.fetchall():
        try:
            cur.execute(f'DROP TABLE IF EXISTS "{t}"')
        except sqlite3.OperationalError:
            pass

    cur.execute(f'PRAGMA table_info("{layer_name}")')
    columns = [col[1] for col in cur.fetchall()]

    for field_name in field_names:
        if field_name in columns:
            try:
                cur.execute(f'ALTER TABLE "{layer_name}" DROP COLUMN {field_name}')
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()

def deleteIndexes(gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'index'
        AND tbl_name = ?
    """, (layer_name,))

    indexes = [row[0] for row in cur.fetchall()]

    for idx in indexes:
        if idx.startswith("idx_"):
            try:
                cur.execute(f'DROP INDEX IF EXISTS "{idx}"')
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()

def getFieldPropertiesGPKG(gpkg_path, layer_name, attribute_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    cur.execute(f'PRAGMA table_info("{layer_name}")')
    columns = cur.fetchall()
    conn.close()

    fields = []
    base_type = None
    length = None
    for col in columns:
        name = col[1]
        if name == attribute_name:
            type_def = col[2]  # e.g., VARCHAR(50) or TEXT or INTEGER
            length = None
            if '(' in type_def and ')' in type_def:
                try:
                    length = int(type_def[type_def.find('(')+1:type_def.find(')')])
                except ValueError:
                    length = None
            base_type = type_def.split('(')[0].upper()

    return base_type, length

def getHoldersHoldingsGPKG(gpkg_path, layer_name, holder_field, attributeName):
    """
    DESCRIPTION: Create a dictionary for holder and their holdings
    INPUTS:
            layer: QgsVectorLayer
    OUTPUTS: Dictionary, key: holders ids, values: List, holdings ids
    """
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    holding_column = attributeName

    cur.execute(f"""
        SELECT "{holder_field}", "{holding_column}"
        FROM "{layer_name}"
        WHERE "{holder_field}" IS NOT NULL
        ORDER BY "{holder_field}"
    """)
    
    holders_with_holdings = defaultdict(list)
    holders_holding_number = defaultdict(int)

    for holder, holding in cur.fetchall():
        holders_with_holdings[str(holder)].append(holding)
        holders_holding_number[str(holder)] += 1

    conn.close()
    return dict(holders_with_holdings), dict(holders_holding_number)

def getHoldingsAreasGPKG(gpkg_path, layer_name, area_field, id_field):
    """
    DESCRIPTION: Create a dictionary for holding and its area
    INPUTS:
            layer: QgsVectorLayer
            areaId: Integer, ID of the polygon feature
            idAttribute: Attribute name of the ID field
    OUTPUTS: Dictionary, key: holding id, values: Integer, area
    """
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    uri = f"{gpkg_path}|layername={layer_name}"
    layer = QgsVectorLayer(uri, layer_name, "ogr")

    for feature in layer.getFeatures():
        holding_id = feature[id_field]
        area_value = feature[area_field]

    cur.execute(f"""
        SELECT "{id_field}", "{area_field}"
        FROM "{layer_name}"
        WHERE "{id_field}" IS NOT NULL
    """)

    holdings_areas = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    layer = None

    return holdings_areas

def getFeatureCountGPKG(gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        SELECT *
        FROM "{layer_name}"
    """)

    count = len([row[0] for row in cur.fetchall()])

    conn.close()
    return count

def getFieldNamesGPKG(gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"PRAGMA table_info('{layer_name}')")
    columns = [row[1] for row in cur.fetchall()]

    conn.close()
    return columns


def determineSeedPolygonsGPKG(self, gpkg_path, layer_name, selectedFeatures=None):
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
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    holdersWithSeeds = {}
    selectedHolders = []

    cur.execute(f"PRAGMA table_info('{layer_name}')")
    columns = [row[1] for row in cur.fetchall()]
    if 'seed_flag' not in columns:
        cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN seed_flag INTEGER DEFAULT 0')

    if selectedFeatures:
        placeholders = ','.join(['?'] * len(selectedFeatures))
        sql = f"""
            UPDATE "{layer_name}"
            SET seed_flag = 1
            WHERE "{self.idAttribute}" IN ({placeholders})
        """
        cur.execute(sql, selectedFeatures)
        conn.commit()

        cur.execute(f"""
            SELECT DISTINCT "{self.holderAttribute}"
            FROM "{layer_name}"
            WHERE "{self.idAttribute}" IN ({placeholders})
        """, selectedFeatures)
        selectedHolders = [row[0] for row in cur.fetchall()]

    cur.execute(f"""
        WITH ranked AS (
            SELECT "{self.idAttribute}", "{self.holderAttribute}", "{self.weight}",
                   ROW_NUMBER() OVER (
                       PARTITION BY "{self.holderAttribute}"
                       ORDER BY "{self.weight}" DESC
                   ) AS rn
            FROM "{layer_name}"
            WHERE seed_flag IS NULL OR seed_flag = 0
        )
        UPDATE "{layer_name}"
        SET seed_flag = 1
        WHERE "{self.idAttribute}" IN (
            SELECT "{self.idAttribute}" FROM ranked WHERE rn = 1
        )
    """)
    conn.commit()

    if self.useSingle:
        cur.execute(f"""
                UPDATE "{layer_name}"
                SET seed_flag = 0
                WHERE {self.holderAttribute} IN (
                    SELECT {self.holderAttribute}
                    FROM "{layer_name}"
                    GROUP BY {self.holderAttribute}
                    HAVING COUNT(*) = 1
                )
            """)

    cur.execute(f"""
        SELECT "{self.holderAttribute}", "{self.idAttribute}"
        FROM "{layer_name}"
        WHERE seed_flag = 1
    """)

    cur.execute(f'''
        CREATE INDEX IF NOT EXISTS idx_{layer_name}_seed_flag
        ON "{layer_name}" ("seed_flag")
    ''')


    for holder, holding_id in cur.fetchall():
        holdersWithSeeds.setdefault(holder, []).append(holding_id)

    

    conn.close()
    return 'seed_flag'

def createDistanceMatrixGPKG(self, gpkg_path, layer_name, context, feedback, nearestPoints=0, simply=False):
    #DESCRIPTION: Create a distance matrix of the input layer features
    #INPUTS:
    #        layer: QgsVectorLayer
    #OUTPUTS: Dictionary, key: holding id, values: Distionary (nested), key: holding ids, values: Float, distances
    uri = f"{gpkg_path}|layername={layer_name}"
    layer = QgsVectorLayer(uri, layer_name, "ogr")

    algParams = {
        'INPUT': layer,
        'ALL_PARTS': False,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    centroids_out = processing.run("native:centroids", algParams, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

    centroids = QgsProcessingUtils.mapLayerFromString(centroids_out, context)

    if simply and not nearestPoints:
        import numpy as np
        import json
        from scipy.spatial import cKDTree
        distanceMatrix2 = {}
        points = []
        fids = []

        for feature in centroids.getFeatures():
            geom = feature.geometry().asPoint()
            points.append((geom.x(), geom.y()))
            fids.append(feature.attribute(self.idAttribute))

        points_A = np.array(points)

        tree = cKDTree(points_A)

        bbox = layer.extent()
        width = bbox.width()
        height = bbox.height()
        diagonal = math.sqrt(width**2 + height**2)

        results = tree.query_ball_point(points_A, r=diagonal)

        for i, result in enumerate(results):
            distanceMatrix2[fids[i]] = {}
            for j in result:
                if i == j:
                    continue
                dist = np.linalg.norm(points_A[i] - points_A[j])
                distanceMatrix2[fids[i]][fids[j]] = dist
        del points_A, tree, results
        return distanceMatrix2
    
    algParams = {
        'INPUT': centroids,
        'INPUT_FIELD': self.idAttribute,
        'TARGET': centroids,
        'TARGET_FIELD': self.idAttribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT': 'TEMPORARY_OUTPUT'
    }
    if simply and nearestPoints:
        algParams['MATRIX_TYPE'] = 0
        algParams['NEAREST_POINTS'] = nearestPoints
    matrix_string = processing.run("qgis:distancematrix", algParams, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']
    matrix = QgsProcessingUtils.mapLayerFromString(matrix_string, context)
    
    if simply and nearestPoints:
        distanceMatrix = {}
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
                if field != 'ID':
                    tempDict[field] = value
            distanceMatrix[feature.attribute('ID')] = tempDict

    return distanceMatrix

def calculateTotalDistancesGPKG(self, gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    conn.enable_load_extension(True)
    try:
        conn.load_extension("mod_spatialite")
    except OSError:
        pass
    cur = conn.cursor()

    cur.execute(f'PRAGMA table_info("{layer_name}")')
    columns = [row[1] for row in cur.fetchall()]
    if 'total_distance' not in columns:
        cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN total_distance REAL DEFAULT 0.0')

    if 'total_distance_after' not in columns:
        cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN total_distance_after REAL')

    cur.execute(
        f"""
        UPDATE "{layer_name}" AS v
        SET total_distance = COALESCE((
            SELECT SUM(d.distance)
            FROM "{layer_name}" AS seed
            JOIN "{layer_name}" AS h
                ON h.{self.holderAttribute} = seed.{self.holderAttribute}
            LEFT JOIN "{self.distanceMatrixTable}" d
                ON d.input_id = seed.{self.idAttribute}
            AND d.target_id = h.{self.idAttribute}
            WHERE seed.seed_flag = 1
            AND seed.{self.holderAttribute} = v.{self.holderAttribute}
        ), 0)
    """
    )

    cur.execute(f"""
    UPDATE "{layer_name}"
    SET total_distance_after = total_distance
    """)

    conn.commit()
    conn.close()
    
    return 'total_distance', 'total_distance_after'

def calculateStatDataGPKG(self,  gpkg_path, layer_name, stat_layer_name, prefix, fieldName):
    """
    DESCRIPTION: Calculate statistics infos about a layer and fields
    INPUTS:
            layer: QgsVectorLayer
            fieldName: name of the field
    OUTPUTS: Dictionary - Holder - # of holdings - Area - Average distance
    """
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        UPDATE "{stat_layer_name}" AS s
        SET
            "{prefix} # of polygons (HFI)" = COALESCE(stats.ParcelNumber, 0),
            "{prefix} Sum balancing value (PFI)" = COALESCE(stats.TotalArea, 0),
            "{prefix} Distance (m) (HDI)" = COALESCE(
                CASE WHEN stats.ParcelNumber = 0 THEN 0
                     ELSE stats.TotalDistance / stats.ParcelNumber
                END, 0
            )
        FROM (
            SELECT
                {fieldName} AS holder,
                COUNT({self.idAttribute}) AS ParcelNumber,
                SUM(COALESCE({self.weight}, 0)) AS TotalArea,
                SUM(COALESCE({self.totalDistance}, 0))/COUNT({self.idAttribute}) AS TotalDistance
            FROM "{layer_name}"
            GROUP BY {fieldName}
        ) AS stats
        WHERE s."Holder ID" = stats.holder
    """)

    conn.commit()
    conn.close()

def calculateStatDataMergedGPKG(self, gpkg_path, layer, fieldName, timestamp):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()
    table_name = f"temp_data_{timestamp}"

    cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')

    cur.execute(f"""
        CREATE TABLE "{table_name}" (
            "Holder ID" TEXT,
            "ParcelNumber" REAL,
            "TotalArea" REAL,
            "AverageDistance" REAL
        )
    """)

    cur.execute(f"""
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description
        )
        VALUES (?, 'attributes', ?, 'Indicator table')
    """, (table_name, table_name))

    cur.execute(f"""
        INSERT INTO "{table_name}" (
            "Holder ID",
            "ParcelNumber",
            "TotalArea",
            "AverageDistance"
        )
        SELECT
            {fieldName} AS "Holder ID",
            COUNT({self.idAttribute}) AS ParcelNumber,
            SUM(COALESCE({self.weight}, 0)) AS TotalArea,
            CASE
                WHEN COUNT({self.idAttribute}) = 0 THEN 0
                ELSE SUM(COALESCE({self.totalDistance}, 0))/COUNT({self.idAttribute})/COUNT({self.idAttribute})
            END AS AverageDistance
        FROM "{layer}"
        GROUP BY "{fieldName}"
    """)

    conn.commit()
    conn.close()

    return table_name

def calculateIndexDataGPKG(gpkg_path, stat_layer_name, prefix, temp_layer):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    # Update with SQL
    cur.execute(f"""
        UPDATE "{stat_layer_name}" AS s
        SET
            "{prefix} HFI" = 
                CASE 
                    WHEN s."{prefix} # of polygons (HFI)" = 0 THEN 0
                    ELSE (1 - (t.ParcelNumber / s."{prefix} # of polygons (HFI)")) * 100
                END,

            "{prefix} PFI" =
                CASE 
                    WHEN s."{prefix} # of polygons (HFI)" = 0 THEN 0
                    ELSE ROUND(s."{prefix} Sum balancing value (PFI)" / s."{prefix} # of polygons (HFI)", 3)
                END,

            "{prefix} HDI" =
                CASE 
                    WHEN s."{prefix} Distance (m) (HDI)" = 0 THEN 0
                    ELSE (1 - (t.AverageDistance / s."{prefix} Distance (m) (HDI)")) * 100
                END

        FROM "{temp_layer}" t
        WHERE s."Holder ID" = t."Holder ID"
    """)

    conn.commit()
    conn.close()

def calculateIndexDifferencesGPKG(self, gpkg_path, temp_layer, stat_layer_name, changes):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        UPDATE "{stat_layer_name}" AS s
        SET
            "CH # of polygons (HFI)" = ABS(s."BE # of polygons (HFI)" - s."AE # of polygons (HFI)"),
            "CH Sum balancing value (PFI)" = ABS(s."BE Sum balancing value (PFI)" - s."AE Sum balancing value (PFI)"),
            "CH Distance (m) (HDI)" = ABS(s."BE Distance (m) (HDI)" - s."AE Distance (m) (HDI)")

        FROM "{temp_layer}" t
        WHERE s."Holder ID" = t."{self.holderAttribute}"
    """)
    conn.commit()

    cur.execute(f"""
        UPDATE "{stat_layer_name}"
        SET
            "CH HFI" = CASE
                WHEN "BE # of polygons (HFI)" = 0 THEN 0
                ELSE ("CH # of polygons (HFI)" / "BE # of polygons (HFI)") * 100
            END,
            "CH PFI" = CASE
                WHEN "BE Sum balancing value (PFI)" = 0 THEN 0
                ELSE ("CH Sum balancing value (PFI)" / "BE Sum balancing value (PFI)") * 100
            END,
            "CH HDI" = CASE
                WHEN "BE Distance (m) (HDI)" = 0 THEN 0
                ELSE ("CH Distance (m) (HDI)" / "BE Distance (m) (HDI)") * 100
            END
    """)


    cur.executemany(f"""
        UPDATE "{stat_layer_name}"
        SET "Change num" = ?
        WHERE "Holder ID" = ?
    """, [(v, k) for k, v in changes.items()])

    conn.commit()
    conn.close()

def getSelectionIdsGPKG(layer):
    selectedFeatures = layer.selectedFeatures()
    ids = []
    for feature in selectedFeatures:
        ids.append(feature.id())
    
    return ids

def checkSeedNumberGPKG(self, feedback):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        SELECT 1
        FROM "{layer_name}"
        WHERE "seed_flag" = 1
        GROUP BY {self.holderAttribute}
        HAVING COUNT({self.idAttribute}) > 1
        LIMIT 1
    """)

    result = cur.fetchone()

    conn.close()

    if result:
        feedback.reportError('More than one feature preference for one holder - plugin stop') 
        return False
    return True

def getChangableHoldingsGPKG(self, inDistance=None):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    inDistanceClause = f'AND "{self.idAttribute}" IN ({",".join("?" for _ in inDistance)})' if inDistance else ''

    cur.execute(f"""
            SELECT "{self.idAttribute}"
            FROM "{layer_name}"
            WHERE "seed_flag" = 0 {inDistanceClause}
            """)
    changableHoldings = [row[0] for row in cur.fetchall()]
    conn.close()
    return changableHoldings

def setTurnAttributesGPKG(self, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()
    newId = f"id_{self.turn}"
    newHolder = f"holder_{self.turn}"

    cur.execute(f'DROP TABLE IF EXISTS {newId}')
    cur.execute(f'DROP TABLE IF EXISTS {newHolder}')
    
    cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN {newId} TEXT')
    cur.execute(f'ALTER TABLE "{layer_name}" ADD COLUMN {newHolder} TEXT')

    cur.execute(f'UPDATE "{layer_name}" SET {newHolder} = {self.actualHolderAttribute}')

    conn.commit()   
    if not connection:
        conn.close()

    return newId, newHolder

def calculateNeighboursGPKG(self, feedback, context=None):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    conn.enable_load_extension(True)
    try:
        conn.load_extension("mod_spatialite")
    except Exception as e:
        feedback.reportError(f"Error loading spatialite extension: {e}")
    cur = conn.cursor()
    table_name = "neighbours"

    cur.execute(f'SELECT geom, {self.idAttribute} FROM "{layer_name}"')
    data = [(row[0], row[1]) for row in cur.fetchall()]
    geometries, ids = zip(*data)

    cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')

    cur.execute("""
    DELETE FROM gpkg_contents 
    WHERE table_name = ? OR identifier = ?
    """, (table_name, table_name))

    cur.execute(f"""
        CREATE TABLE "{table_name}" (
            source_id TEXT,
            target_id TEXT
        )
    """)

    cur.execute(f"""
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description
        )
        VALUES (?, 'attributes', ?, 'Neighbours table')
    """, (table_name, table_name))

    for turn, g in enumerate(geometries):
        cur.execute(f"""
            INSERT INTO "{table_name}" (source_id, target_id)
            SELECT
                ? AS source_id,
                {self.idAttribute} AS target_id
            FROM "{layer_name}" 
            WHERE
                ST_Touches(GeomFromGPB(?), GeomFromGPB(geom))
        """, (ids[turn], g,))
    #for g in geometries:
    #    cur.execute(f'''SELECT parcel_uid, ST_Touches(GeomFromWKB(substr(?, 9)), geom) FROM "{layer_name}" WHERE ST_Touches(SetSRID(GeomFromWKB(substr(?, 9)), ), geom)''', (g, g,))
    #    cur.execute(f'''SELECT parcel_uid FROM "{layer_name}" WHERE ST_Touches(GeomFromWKB(substr(?, 9)), geom)''', (g,))
    #    print(len(cur.fetchall()))

    cur.execute(f'CREATE INDEX idx_neigh_source ON "{table_name}" (source_id)')
    cur.execute(f'CREATE INDEX idx_neigh_target ON "{table_name}" (target_id)')
 
    conn.commit()
    conn.close()

    return table_name

def querySeeds(self, holder, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    cur.execute(f"""
        SELECT {self.idAttribute}
        FROM "{layer_name}"
        WHERE seed_flag > 0 AND "{self.actualHolderAttribute}" = ?
        ORDER BY seed_flag ASC
    """, (holder,))
    seeds = [row[0] for row in cur.fetchall()]

    if not connection:
        conn.close()
    return seeds

def queryNeighbours(self, holder, seed, neighbours_table, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    cur.execute(f"""
        SELECT n.target_id
        FROM "{neighbours_table}" n
        JOIN "{layer_name}" t
            ON t.{self.idAttribute} = n.target_id
        WHERE n.source_id = ?
        AND t.{self.actualHolderAttribute} != ? AND t.{self.actualIdAttribute} IS NULL AND t.seed_flag = 0
    """, (seed, holder))

    neighboursIds = [row[0] for row in cur.fetchall()]

    if not connection:
        conn.close()
    return neighboursIds

def queryAllHolderItem(self, holder, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    cur.execute(f"""
        SELECT {self.idAttribute}
        FROM "{layer_name}"
        WHERE {self.actualHolderAttribute} = ?
    """, (holder,))

    allItems = [row[0] for row in cur.fetchall()]

    if not connection:
        conn.close()
    return allItems

def queryChangableItems(self, holder, seed):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        SELECT l.{self.idAttribute}
        FROM "{layer_name}" l
        JOIN "{self.distanceMatrixTable}" d
            ON d.target_id = l.{self.idAttribute}
        WHERE
            d.input_id = ?
            AND d.distance <= ?
            AND l.{self.actualHolderAttribute} = ?
            AND l.seed_flag = 0
            AND l.{self.actualIdAttribute} IS NULL
    """, (seed, self.distance, holder))

    changables = [row[0] for row in cur.fetchall()]

    conn.close()
    return changables

def queryChangableItemsWithoutDistance(self, holder, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    cur.execute(f"""
        SELECT l.{self.idAttribute}
        FROM "{layer_name}" l
        WHERE
            l.{self.actualHolderAttribute} = ?
            AND l.seed_flag = 0
            AND l.{self.actualIdAttribute} IS NULL
    """, (holder,))

    changables = [row[0] for row in cur.fetchall()]

    if not connection:
        conn.close()
    return changables

def queryHolder(self, holdingId, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    cur.execute(f"""
        SELECT {self.actualHolderAttribute}
        FROM "{layer_name}"
        WHERE {self.idAttribute} = ?
    """, (holdingId,))

    holder = cur.fetchone()[0]

    if not connection:
        conn.close()
    return holder

def queryMaxDistance(self, seed, combination):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    params = [seed]

    placeholders = ",".join("?" for _ in combination)
    queryPart = f" AND target_id IN ({placeholders})"
    params.extend(combination)

    cur.execute(f"""
        SELECT COALESCE(MAX(distance), 0)
        FROM "{self.distanceMatrixTable}"
        WHERE input_id = ? {queryPart}
    """, params)

    result = cur.fetchone()
    return result[0]

def queryTotalDistance(self, seed, combination):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in combination) if combination else ""

    cur.execute(f"""
        SELECT
            COALESCE(SUM(distance), 0)
        FROM "{self.distanceMatrixTable}"
        WHERE input_id = ?
        AND target_id IN ({placeholders})
    """, (seed, *combination))

    result = cur.fetchone()
    return result[0]

def queryHoldings(self, holder):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        SELECT "{self.idAttribute}"
        FROM "{layer_name}"
        WHERE "{self.holderAttribute}" = ?
    """, (holder,))

    holdings = [row[0] for row in cur.fetchall()]

    conn.close()
    return holdings

def setAttributeValuesGPKG(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()
    else:
        conn = connection
        cur = conn.cursor()

    placeholdersHolder = ",".join("?" for _ in holderCombinationForChange)
    placeholdersNeighbour = ",".join("?" for _ in neighbourCombinationForChange)

    cur.execute(f"""
        UPDATE "{layer_name}"
        SET "{self.actualHolderAttribute}" = ?
        WHERE "{self.idAttribute}" IN ({placeholdersHolder})
    """, (neighbourHolder, *holderCombinationForChange))

    cur.execute(f"""
        UPDATE "{layer_name}"
        SET "{self.actualHolderAttribute}" = ?
        WHERE "{self.idAttribute}" IN ({placeholdersNeighbour})
    """, (holder, *neighbourCombinationForChange))

    conn.commit()

    placeholdersHolderValue = ",".join(map(str, holderCombinationForChange))
    placeholdersNeighbourValue = ",".join(map(str, neighbourCombinationForChange))

    cur.execute(f"""
        UPDATE "{layer_name}"
        SET "{self.actualIdAttribute}" = ?
        WHERE "{self.idAttribute}" IN ({placeholdersHolder})
    """, (placeholdersNeighbourValue, *holderCombinationForChange))

    cur.execute(f"""
        UPDATE "{layer_name}"
        SET "{self.actualIdAttribute}" = ?
        WHERE "{self.idAttribute}" IN ({ placeholdersNeighbour})
    """, (placeholdersHolderValue , *neighbourCombinationForChange))

    conn.commit()
    if not connection:
        conn.close()

def queryDistance(self, seed, holdingId):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute(f"""
        SELECT distance
        FROM "{self.distanceMatrixTable}"
        WHERE input_id = ? AND target_id = ?
    """, (seed, holdingId))

    result = cur.fetchone()
    return result[0]

def update_distancesGPKG(self, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
    else:
        conn = connection
    conn.enable_load_extension(True)
    try:
        conn.load_extension("mod_spatialite")
    except OSError:
        pass
    cur = conn.cursor()

    # update distances
    cur.execute(
        f"""
        UPDATE "{layer_name}" AS v
        SET {self.totalDistance} = COALESCE((
            SELECT SUM(d.distance)
            FROM "{layer_name}" AS seed
            JOIN "{layer_name}" AS h
                ON h.{self.actualHolderAttribute} = seed.{self.actualHolderAttribute}
            LEFT JOIN "{self.distanceMatrixTable}" d
                ON d.input_id = seed.{self.idAttribute}
            AND d.target_id = h.{self.idAttribute}
            WHERE seed.seed_flag = 1
            AND seed.{self.actualHolderAttribute} = v.{self.actualHolderAttribute}
        ), 0)
    """
    )

    conn.commit()
    if not connection:
        conn.close()

def update_seedsGPKG(self, holdingId, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
    else:
        conn = connection
    cur = conn.cursor()

    cur.execute(f"""
        UPDATE "{layer_name}"
        SET seed_flag = 2
        WHERE {self.idAttribute} = ?
    """, (holdingId,))

    conn.commit()
    if not connection:
        conn.close()

def update_changeLogGPKG(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, connection=False):
    gpkg_path, layer_name = self.layer
    if not connection:
        conn = sqlite3.connect(gpkg_path)
    else:
        conn = connection
    cur = conn.cursor()

    cur.execute(f"""
        INSERT INTO "{self.changeLog}" (
            "Change_ID",
            "Get_from_holder_ID",
            "Get_from_polygon_ID",
            "Transfer_to_holder_ID",
            "Transfer_to_polygon_ID"
        ) VALUES (?, ?, ?, ?, ?)
    """, (
        self.counter,
        holder,
        ",".join(map(str, holderCombinationForChange)),
        neighbourHolder,
        ",".join(map(str, neighbourCombinationForChange))
    ))

    conn.commit()
    if not connection:
        conn.close()

def update_holdersHoldingsNumberGPKG(self, holder, targetHolder, holderCombinationForChange, targetCombinationForChange):
    self.holdersHoldingNumber[holder] += len(targetCombinationForChange) - len(holderCombinationForChange)
    self.holdersHoldingNumber[targetHolder] += len(holderCombinationForChange) - len(targetCombinationForChange)

def saveInteractionOutput1GPKG(self):
    #import ptvsd
    #ptvsd.debug_this_thread()
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    output_table = f"{layer_name}_exchange_frequency"

    cur.execute(f'DROP TABLE IF EXISTS "{output_table}"')
    cur.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (output_table,))

    cur.execute(f"""
            CREATE TABLE "{output_table}" (
                "from" TEXT,
                "to" TEXT,
                "weight" INTEGER
            )
        """)
    
    cur.execute("""
        INSERT INTO gpkg_contents (table_name, data_type, identifier, description)
        VALUES (?, 'attributes', ?, 'Interaction log')
    """, (output_table, output_table))

    beforeHoldersWithHoldings, _ = getHoldersHoldingsGPKG(gpkg_path, layer_name, self.holderAttribute, self.idAttribute)
    afterHoldersWithHoldings, _ = getHoldersHoldingsGPKG(gpkg_path, layer_name, self.actualHolderAttribute, self.idAttribute)
    holders = list(self.holdersWithHoldings.keys())
    holders.sort()

    interactionTable = {}   
    for holder in holders:
        if holder not in interactionTable:
            interactionTable[holder] = {}
        fromHolderHoldings = beforeHoldersWithHoldings[holder]
        for holderAgain in holders:
            if holderAgain not in interactionTable:
                interactionTable[holderAgain] = {}
            if holder != holderAgain:
                toHolderHoldings = afterHoldersWithHoldings[holderAgain]
                exchangeNum = len(fromHolderHoldings) - len(list(set(fromHolderHoldings) - set(toHolderHoldings)))
                interactionTable[holder][holderAgain] = exchangeNum

    changes = {}
    for holder, holderList in interactionTable.items():
        changeValue = sum([interactionTable[holder][holderAgain] for holderAgain in holderList])
        changes[holder] = changeValue

    fromList = []
    toList = []
    weightList = []
    for holder in holders:
        for holderAgain in holders:
            if holder == holderAgain:
                continue
            interactionNum = interactionTable[holder][holderAgain]
            if interactionNum != 0:
                fromAttribute = holder
                toAttribute = holderAgain
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

    rows = list(zip(fromList, toList, weightList))
    rows.sort()

    cur.executemany(f"""
        INSERT INTO "{output_table}" ("from", "to", "weight")
        VALUES (?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()

    return output_table, changes


def saveInteractionOutput2GPKG(self):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    output_table = f"{layer_name}_swap_frequency"

    cur.execute(f'DROP TABLE IF EXISTS "{output_table}"')
    cur.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (output_table,))

    cur.execute(f"""
            CREATE TABLE "{output_table}" (
                "from" TEXT,
                "to" TEXT,
                "weight" INTEGER
            )
        """)
    
    cur.execute("""
        INSERT INTO gpkg_contents (table_name, data_type, identifier, description)
        VALUES (?, 'attributes', ?, 'Interaction log')
    """, (output_table, output_table))

    holders = list(self.holdersWithHoldings.keys())
    holders.sort()

    fromList = []
    toList = []
    weightList = []
    for holder in holders:
        for holderAgain in holders:
            interactionNum = self.interactionTable[holder][holderAgain]
            if interactionNum != 0:
                fromAttribute = holder
                toAttribute = holderAgain
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

    rows = list(zip(fromList, toList, weightList))
    rows.sort()

    cur.executemany(f"""
        INSERT INTO "{output_table}" ("from", "to", "weight")
        VALUES (?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()

    return output_table

def createExchangeLog(self):
    gpkg_path, layer_name = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    output_table = f"{layer_name}_change_log"

    cur.execute(f'DROP TABLE IF EXISTS "{output_table}"')
    cur.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (output_table,))

    cur.execute(f"""
            CREATE TABLE "{output_table}" (
                "Change_ID" INTEGER,
                "Get_from_holder_ID" TEXT,
                "Get_from_polygon_ID" TEXT,
                "Transfer_to_holder_ID" TEXT,
                "Transfer_to_polygon_ID" TEXT
            )
        """)
    
    cur.execute("""
        INSERT INTO gpkg_contents (table_name, data_type, identifier, description)
        VALUES (?, 'attributes', ?, 'Change log')
    """, (output_table, output_table))
    

    conn.commit()
    conn.close()

    return output_table

def maxDistance(self, seed, featureIds):
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
            distance = queryDistance(self, seed, featureId)
            if distance > maxDistance:
                maxDistance = distance
    return maxDistance

def avgDistance(self, seed, featureIds):
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
            distance = queryDistance(self, seed, featureId)
            sumDistance += distance
    return sumDistance / divider

def sortHolderWithHoldings(holdersWithHoldings):
    sortedHoldersWithHoldings = dict(sorted(holdersWithHoldings.items(), key=lambda item: len(item[1]), reverse=True))
    return sortedHoldersWithHoldings

def dropTriggers(gpkg_path, layer_name):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT type, name
        FROM sqlite_master
        WHERE (
            sql LIKE '%ST_%'
            OR name LIKE 'rtree_%'
        )
    """)

    rows = cur.fetchall()

    for obj_type, name in rows:

        try:
            if obj_type == 'trigger':
                cur.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        except Exception as e:
            print("ERROR:", name, e)

    conn.commit()
    conn.close()

def neighboursGPKG(self, feedback, totalAreas=None, context=None):
    maxTurn = 10
    localChanges = copy.deepcopy(self.counter)
    changer = True

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Neighbours algorithm start')

    feedback.pushInfo(f'Calculating neighbours...')
    neighbours = calculateNeighboursGPKG(self, feedback, context)
    feedback.pushInfo(f'Neighbours calculated')

    while changer:
        self.turn += 1
        maxTurn -= 1
        changedOnce = []
        connection = connectToDB(self.layer[0])
        self.actualIdAttribute, self.actualHolderAttribute = setTurnAttributesGPKG(self, connection)
        feedback.pushInfo(f'Round {self.turn}')
        for holder in self.holdersWithHoldings.keys():
            if holder == 'NULL':
                continue

            seeds = querySeeds(self, holder, connection)

            #feedback.pushInfo(f'Holder {holder} - Seeds: {seeds}')
            
            if not seeds:
                continue

            if self.simply:
                MAXCANDIDATES = self.simply
                MAXCOMBTURN = min(1000*MAXCANDIDATES, 100000)
                candidate = 0
                combTurn = 0

            for seed in seeds:
                neighboursIds = queryNeighbours(self, holder, seed, neighbours, connection)
                try:
                    targetHoldings = list(self.filteredDistanceMatrix[seed].keys())
                except KeyError:
                    continue

                #feedback.pushInfo(f'Holder {holder} - Seeds: {seeds} - Neighbours: {neighboursIds}')

                for nghID in neighboursIds:
                    holderTotalArea = holdersLocalTotalArea[holder]
                    holderChangables = queryChangableItemsWithoutDistance(self, holder, connection)
                    holderChangables = [holding for holding in holderChangables if holding in targetHoldings and holding not in changedOnce]
                    if self.strictHDI:
                        holderAllItems = [item for item in queryAllHolderItem(self, holder, connection) if item != seed]
                        holderMaxDistance = maxDistance(self, seed, holderAllItems)
                        holderAvgDistance = avgDistance(self, seed, holderAllItems)

                    #feedback.pushInfo(f'HolderChangables for holder {holder} and seed {seed}: {len(holderChangables)}')

                    if not holderChangables:
                        continue
                    
                    # Get ngh holder name
                    neighbourHolder = queryHolder(self, nghID)
                    if neighbourHolder != 'NULL' and neighbourHolder != holder:
                        try:
                            neighbourHolderSeed = querySeeds(self, neighbourHolder, connection)[0]
                        except IndexError:
                            if self.useSingle:
                                neighbourHolderSeed = False
                            else:
                                continue
                    # Get holder total area
                    neighbourHolderTotalArea = holdersLocalTotalArea[neighbourHolder]
                    # Get holders holdings
                    neighbourChangables = queryChangableItemsWithoutDistance(self, neighbourHolder, connection)
                    neighbourChangables = [holding for holding in neighbourChangables if holding in targetHoldings and holding not in changedOnce]

                    #feedback.pushInfo(f'NeighbourChangables for neighbour holder {neighbourHolder} and seed {neighbourHolderSeed}: {len(neighbourChangables)}')
                    if not neighbourChangables:
                        continue

                    if self.strictHDI:
                        targetAllItems = [item for item in queryAllHolderItem(self, neighbourHolder, connection) if item != neighbourHolderSeed]
                        targetMaxDistance = maxDistance(self, neighbourHolderSeed, targetAllItems)
                        targetAvgDistance = avgDistance(self, neighbourHolderSeed, targetAllItems)

                    if not nghID in neighbourChangables:
                        continue

                    if not neighbourHolderSeed:
                        neighbourHoldingsCombinations = [[nghID]]
                    else: 
                        neighbourHoldingsCombinations = \
                            vgle_utils.combine_with_constant_in_all(
                                neighbourChangables, nghID)

                    holdingCombinations = vgle_utils.combine_with_constant_in_all(holderChangables)

                    holderCombinationForChange = None
                    neighbourCombinationForChange = None
                    holderNewTotalArea = 0
                    neighbourNewTotalArea = 0
                    totalAreaDifference = None
                    
                    for combination in holdingCombinations:
                        combinationLenght = len(combination)

                        for neighbourCombination in neighbourHoldingsCombinations:                
                            neighbourCombinationLenght = len(neighbourCombination)

                            if self.simply:
                                if combTurn >= MAXCOMBTURN:
                                    break
                                else:
                                    combTurn += 1

                            #feedback.pushInfo(f'Combination turn - {lenTurn}')

                            # Base condition: weight           
                            temporaryHolderArea = vgle_utils.calculateCombinationArea(self, combination)       
                            temporaryTargetArea = vgle_utils.calculateCombinationArea(self, neighbourCombination)        
                            newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                            newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                            thresholdHolder = vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder)
                            thresholdNeighbour = vgle_utils.checkTotalAreaThreshold(self, newNeighbourTotalArea, neighbourHolder)
                            difference = abs(newHolderTotalArea-holderTotalArea)
                            if thresholdHolder and thresholdNeighbour:
                                # Parcel number condition
                                if self.strictHFI:
                                    holderNewHoldignNum = self.holdersHoldingNumber[holder] - combinationLenght + neighbourCombinationLenght
                                    targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - neighbourCombinationLenght + combinationLenght
                                    if holderNewHoldignNum > self.holdersHoldingNumber[holder] or targetNewHoldingNum > self.holdersHoldingNumber[neighbourHolder]:
                                        continue

                                # Distance conditions
                                if self.strictHDI:
                                    holderNewAllItems = [item for item in queryAllHolderItem(self, holder) if item != seed] + neighbourCombination - combination
                                    holderNewMaxDistance = maxDistance(self, seed, holderNewAllItems)
                                    holderNewAvgDistance = avgDistance(self, seed, holderNewAllItems)

                                    targetNewAllItems = [item for item in queryAllHolderItem(self, neighbourHolder) if item != neighbourHolderSeed] + combination - neighbourCombination
                                    targetNewMaxDistance = maxDistance(self, neighbourHolderSeed, targetNewAllItems)
                                    targetNewAvgDistance = avgDistance(self, neighbourHolderSeed, targetNewAllItems)/len(targetNewAllItems)

                                    # Max Distance condition
                                    if holderMaxDistance < holderNewMaxDistance or targetMaxDistance < targetNewMaxDistance:
                                        continue

                                    # Average Distance condition
                                    if holderAvgDistance < holderNewAvgDistance or targetAvgDistance < targetNewAvgDistance:
                                        continue

                                if self.simply:
                                    if candidate >= MAXCANDIDATES:
                                        break
                                    else:
                                        candidate += 1

                                if totalAreaDifference is None:
                                    #feedback.pushInfo(f'Possible combination: {combination}')
                                    holderCombinationForChange = combination
                                    neighbourCombinationForChange = neighbourCombination
                                    holderNewTotalArea = newHolderTotalArea
                                    neighbourNewTotalArea = newNeighbourTotalArea
                                    totalAreaDifference = difference
                                else:
                                    if difference < totalAreaDifference:
                                        holderCombinationForChange = combination
                                        neighbourCombinationForChange = neighbourCombination
                                        holderNewTotalArea = newHolderTotalArea
                                        neighbourNewTotalArea = newNeighbourTotalArea
                                        totalAreaDifference = difference
                    if holderCombinationForChange and neighbourCombinationForChange:
                        self.counter += 1   
                        setAttributeValuesGPKG(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, connection)
                        if self.stats:
                            self.interactionTable[holder][neighbourHolder] += 1
                            self.interactionTable[neighbourHolder][holder] += 1    
                        update_distancesGPKG(self, connection)
                        update_seedsGPKG(self, nghID, connection)
                        update_changeLogGPKG(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, connection)
                        update_holdersHoldingsNumberGPKG(self,  holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                        commitMessage = f'Change {self.counter} for {neighbourCombinationForChange} (holder:{neighbourHolder}) to get neighbour of {seed} (holder:{holder}): ' \
                                        f'{holderCombinationForChange} for {neighbourCombinationForChange}'
                        logging.debug(commitMessage)
                        feedback.pushInfo(commitMessage)

                        holdersLocalTotalArea[holder] = holderNewTotalArea
                        holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea

        connection.close()                              

        turnChanges = self.counter - localChanges
        feedback.pushInfo(f'Changes in round {self.turn}: {turnChanges}') 
        logging.debug(f'Changes in round {self.turn}: {turnChanges}')
        if turnChanges == 0:
            # No change happened, no continue in this scope, stop the algorithm
            changer = False
            if self.counter == 0:
                # No change at all, stop the algorithm
                if (self.algorithmIndex == 0 or self.algorithmIndex == 3):
                    # No continue of the algorithm, stop it
                    return None, None
                elif self.algorithmIndex == 2:
                    # Continue with the next method, clear the turn attributes
                    gpkg_path, _ = self.layer
                    deleteTable(gpkg_path, neighbours)
                    return True, holdersLocalTotalArea
        elif maxTurn == 0:
            # Max turn reached, stop the algorithm
            changer = False
        else:
            # Changes happened, continue to the next turn
            localChanges = copy.deepcopy(self.counter)
        feedback.setCurrentStep(1 + self.turn)
        feedback.pushInfo(f'Save turn results to the file')
        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return None, None
        
    return True, holdersLocalTotalArea


def closerGPKG(self, feedback, totalAreas=None, context=None):
    maxTurn = 10
    localChanges = copy.deepcopy(self.counter)
    changer = True

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Closer algorithm start')

    while changer:
        self.turn += 1
        maxTurn -= 1
        changedOnce = []
        connection = connectToDB(self.layer[0])
        self.actualIdAttribute, self.actualHolderAttribute = setTurnAttributesGPKG(self, connection)
        feedback.pushInfo(f'Round {self.turn}')
        
        for holder in self.holdersWithHoldings.keys():
            if holder == 'NULL':
                continue

            seeds = querySeeds(self, holder, connection)
            if not seeds:
                continue
            seed = seeds[0]

            holderAllItems = [item for item in queryAllHolderItem(self, holder, connection) if item != seed]
            if len(holderAllItems) == 0:
                continue

            try:
                targetHoldings = list(self.filteredDistanceMatrix[seed].keys())
                targetHolders = list(set([queryHolder(self, holding, connection) for holding in targetHoldings]))
            except KeyError:
                continue

            if self.simply:
                MAXCANDIDATES = self.simply
                MAXCOMBTURN = min(1000*MAXCANDIDATES, 100000)
                candidate = 0
                combTurn = 0
                if len(targetHolders) > 50:
                    targetHolders = random.choices(targetHolders, k=50)

            for targetHolder in targetHolders:
                changeHolderCombination = None
                changeTargetCombination = None
                changeHolderTotalArea = None
                changeTargetTotalArea = None
                measure = None
                numberOfCandidates = 0

                queryTime = time.time()
                holderChangables = queryChangableItemsWithoutDistance(self, holder, connection)
                holderChangables = [holding for holding in holderChangables if holding in targetHoldings and holding not in changedOnce]
                if not holderChangables:
                    continue

                holderTotalArea = holdersLocalTotalArea[holder]
                holderAllItems = [item for item in queryAllHolderItem(self, holder) if item != seed]
                holderMaxDistance = maxDistance(self, seed, holderAllItems)
                holderAvgDistance = avgDistance(self, seed, holderAllItems)

                try:
                   targetHolderSeed = querySeeds(self, targetHolder, connection)[0]
                except IndexError:
                    if self.useSingle:
                        targetHolderSeed = False
                    else:
                        continue
                
                targetHolderChangables = queryChangableItemsWithoutDistance(self, targetHolder, connection)
                targetHolderChangables = [holding for holding in targetHolderChangables if holding in targetHoldings]
                filteredLocalTargetHoldings = [holding for holding in targetHolderChangables if holding in targetHoldings and holding not in changedOnce]
                targetHolderTotalArea = holdersLocalTotalArea[targetHolder]

                if targetHolderSeed:
                    targetAllItems = [item for item in queryAllHolderItem(self, targetHolder, connection) if item != targetHolderSeed]
                    if len(targetAllItems) == 0:
                        continue
                    targetMaxDistance = maxDistance(self, targetHolderSeed, targetAllItems)
                    targetAvgDistance = avgDistance(self, targetHolderSeed, targetAllItems)
                else:
                    targetMaxDistance = 0
                    targetAvgDistance = 0

                for targetCombination in vgle_utils.combine_with_constant_in_all(filteredLocalTargetHoldings):
                    for holderCombination in vgle_utils.combine_with_constant_in_all(holderChangables):

                        if self.simply:
                            if combTurn >= MAXCOMBTURN:
                                break
                            else:
                                combTurn += 1

                        # Polygon number condition
                        if self.strictHFI:
                            holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(holderCombination) + len(targetCombination)
                            targetNewHoldingNum = self.holdersHoldingNumber[targetHolder] - len(targetCombination) + len(holderCombination)
                            if not holderNewHoldignNum <= self.holdersHoldingNumber[holder] or not targetNewHoldingNum <= self.holdersHoldingNumber[targetHolder]:
                                continue    

                        # Base condition: weight    
                        temporaryHolderArea = vgle_utils.calculateCombinationArea(self, holderCombination)       
                        temporaryTargetArea = vgle_utils.calculateCombinationArea(self, targetCombination)        
                        newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                        newTargetTotalArea = targetHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                        thresholdHolder = vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder)
                        thresholdTarget = vgle_utils.checkTotalAreaThreshold(self, newTargetTotalArea, targetHolder)
                        if not thresholdHolder or not thresholdTarget:
                            continue

                        holderNewAllItems = [item for item in queryAllHolderItem(self, holder, connection) if item != seed and item not in holderCombination] + list(targetCombination)
                        holderNewMaxDistance = maxDistance(self, seed, holderNewAllItems)
                        holderNewAvgDistance = avgDistance(self, seed, holderNewAllItems)

                        targetNewAllItems = [item for item in queryAllHolderItem(self, targetHolder, connection) if item != targetHolderSeed and item not in targetCombination] + list(holderCombination)
                        targetNewMaxDistance = maxDistance(self, targetHolderSeed, targetNewAllItems)
                        targetNewAvgDistance = avgDistance(self, targetHolderSeed, targetNewAllItems)

                        # Max Distance condition
                        if holderMaxDistance < holderNewMaxDistance or targetMaxDistance < targetNewMaxDistance:
                            continue

                        # Average Distance condition
                        if holderAvgDistance < holderNewAvgDistance or targetAvgDistance < targetNewAvgDistance:
                            continue

                        try:
                            weightDifference = abs(newHolderTotalArea-holderTotalArea)/holderTotalArea*100
                        except ZeroDivisionError:
                            weightDifference = 0
                        try:   
                            distanceDifference = abs(holderNewAvgDistance-holderAvgDistance)/holderAvgDistance*100
                        except ZeroDivisionError:
                            distanceDifference = 0
                        localMeasure = weightDifference + distanceDifference

                        if self.simply:
                            if candidate >= MAXCANDIDATES:
                                break
                            else:
                                candidate += 1

                        if measure is None:
                            numberOfCandidates += 1
                            measure = copy.copy(localMeasure)

                            changeHolderCombination = copy.copy(holderCombination)
                            changeTargetCombination = copy.copy(targetCombination)
                            changeHolderTotalArea = copy.copy(newHolderTotalArea)
                            changeTargetTotalArea = copy.copy(newTargetTotalArea)
                        else:
                            if localMeasure < measure:
                                numberOfCandidates += 1
                                measure = copy.copy(localMeasure)

                                changeHolderCombination = copy.copy(holderCombination)
                                changeTargetCombination = copy.copy(targetCombination)
                                changeHolderTotalArea = copy.copy(newHolderTotalArea)
                                changeTargetTotalArea = copy.copy(newTargetTotalArea)

                if measure:
                    self.counter += 1   
                    setAttributeValuesGPKG(self, holder, targetHolder, changeHolderCombination, changeTargetCombination, connection)
                    changedOnce.extend(changeHolderCombination)
                    changedOnce.extend(changeTargetCombination)
                    if self.stats:
                        self.interactionTable[holder][targetHolder] += 1
                        self.interactionTable[targetHolder][holder] += 1    
                    update_distancesGPKG(self, connection)
                    update_changeLogGPKG(self, holder, targetHolder, changeHolderCombination, changeTargetCombination, connection)
                    update_holdersHoldingsNumberGPKG(self,  holder, targetHolder, changeHolderCombination, changeTargetCombination)
                    commitMessage = f'Change {self.counter} for {changeTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): ' \
                                    f'{changeHolderCombination} for {changeTargetCombination}'
                    logging.debug(commitMessage)
                    feedback.pushInfo(commitMessage)

                    holdersLocalTotalArea[holder] = changeHolderTotalArea
                    holdersLocalTotalArea[targetHolder] = changeTargetTotalArea  
        
        connection.commit()
        connection.close()                          

        turnChanges = self.counter - localChanges
        feedback.pushInfo(f'Changes in round {self.turn}: {turnChanges}') 
        logging.debug(f'Changes in round {self.turn}: {turnChanges}')
        if turnChanges == 0:
            # No change happened, no continue in this scope, stop the algorithm
            changer = False
            if self.counter == 0:
                # No change at all, stop the algorithm
                if (self.algorithmIndex == 0 or self.algorithmIndex == 3):
                    # No continue of the algorithm, stop it
                    return None, None
                elif self.algorithmIndex == 2:
                    # Continue with the next method, clear the turn attributes
                    return True, holdersLocalTotalArea
        elif maxTurn == 0:
            # Max turn reached, stop the algorithm
            changer = False
        else:
            # Changes happened, continue to the next turn
            localChanges = copy.deepcopy(self.counter)
        feedback.setCurrentStep(1 + self.turn)
        feedback.pushInfo(f'Save turn results to the file')
        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return None, None
        
    return True, holdersLocalTotalArea
