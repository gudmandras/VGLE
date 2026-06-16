import random, tempfile, time, os, shutil, gc, sqlite3
from datetime import datetime
from pathlib import Path
from osgeo import ogr

from qgis.PyQt.QtCore import QCoreApplication, QVariant, QEventLoop, QTimer
from processing.core.Processing import Processing
from processing.core.ProcessingConfig import ProcessingConfig
from qgis.core import (QgsProject,
                       QgsExpression,
                       QgsSettings,
                       QgsProcessing,
                       QgsApplication,
                       QgsVectorLayer,
                       QgsProcessingAlgorithm,
                       QgsFeatureRequest,
                       QgsProcessingUtils,
                       QgsProcessingMultiStepFeedback,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterEnum,
                       QgsVectorFileWriter,
                       QgsProcessingContext,
                       QgsProcessingParameterField,
                       QgsProcessingParameterString,
                       QgsDataSourceUri,
                       QgsProcessingParameterFile,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterDefinition,
                       QgsProcessingParameterMapLayer,
                       QgsProcessingParameterFolderDestination)
from qgis import processing
import qgis.utils
from . import vgle_layers, vgle_gpkgs, vgle_utils


class JustTopDownAlgorithm(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer',
                                                            types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features',
                                                        defaultValue=False))
        self.addParameter(QgsProcessingParameterField('AssignedByField', 'Holder by field',
                                                      type=QgsProcessingParameterField.Any,
                                                      parentLayerParameterName='Inputlayer'))                                     
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
        
        self.addParameter(QgsProcessingParameterMapLayer('SwapFreq', 'Swap frequency layer'))
        
        #self.addParameter(QgsProcessingParameterFile('csvPath', 'R created csv path',
        #                                                    behavior=QgsProcessingParameterFile.File, fileFilter='CSV files (*.csv)', defaultValue=None))
        
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
        simplfy = QgsProcessingParameterNumber('Simply', "Number of holding combinations to analyze:",
                                                type=QgsProcessingParameterNumber.Integer,
                                                minValue=0, defaultValue=0)
        simplfy.setFlags(simplfy.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(simplfy)
        ralgorithm = QgsProcessingParameterEnum('ralgorithm', 'Desired R algorithm',
                                                       options=['cluster_louvain', 'cluster_leiden'],
                                                       allowMultiple=False, defaultValue='cluster_louvain')
        ralgorithm.setFlags(ralgorithm.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(ralgorithm)

        robjective = QgsProcessingParameterEnum('robjective', 'Desired objective function of the R algorithm (Only works with cluster_louvain)',
                                                       options=['modularity', 'CPM'],
                                                       allowMultiple=False, defaultValue='modularity')
        robjective.setFlags(robjective.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(robjective)

        resolution = QgsProcessingParameterNumber('resolution', 'Resolution value for cluster_louvain R algorithm',
                                                type=QgsProcessingParameterNumber.Double,
                                                minValue=0.0001, maxValue=2.0, defaultValue=0.2)
        resolution.setFlags(resolution.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(resolution)

        self.version = '2026-06-15-01'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return JustTopDownAlgorithm()

    def name(self):
        return 'just_topdown'

    def displayName(self):
        return self.tr('Just top down script')

    def group(self):
        return self.tr('vgle')

    def groupId(self):
        return ''

    def shortHelpString(self):
        try:
            with open(os.path.join(os.path.dirname(__file__), 'shorthelp_topdown.txt'), 'r',
                      encoding='utf-8') as file:
                return file.read()
        except FileNotFoundError:
            return "<html><body><p>Description file not found.</p></body></html>"
        except Exception as e:
            return f"<html><body><p>Error reading description file: {e}</p></body></html>"

    def processAlgorithm(self, parameters, context, model_feedback):
        #import ptvsd
        #ptvsd.debug_this_thread()
        results = {}

        if not is_r_provider_installed():
            model_feedback.reportError('R provider is not installed. Please install R provider to use this algorithm.',
                                 fatalError=True)
            return {}

        R_folder = checkR_folder() 
        if R_folder:
            copy_sucess = copyR_script(os.path.join(os.path.dirname(os.path.abspath(__file__)),'topdown.rsx'))
            copy_sucess = copyR_script(os.path.join(os.path.dirname(os.path.abspath(__file__)),'topdown2.rsx'))
            if not copy_sucess:
                model_feedback.reportError("R Provider - RSX file cannot copied to the Rfolder.")
                return {}
        else:
            model_feedback.reportError("R Provider - R folder is not configured. Cannot install RSX script.")
            return {}

        if not vgle_gpkgs.checkTableName(parameters, model_feedback):
            return {}
        frequency, validSwapFreq = vgle_gpkgs.checkSwapFreq(self.parameterAsVectorLayer(parameters, 'SwapFreq', context))
        if not validSwapFreq:
            model_feedback.reportError(f"swap frequency is not a gpkg layer or it is missing required fields ('to', 'from','weight') in swap frequency layer.")
            return {}
        else:
            self.SwapFreqLayer = validSwapFreq

        feedback = QgsProcessingMultiStepFeedback(2, model_feedback)
        feedback.pushWarning(f"Plugin version: {self.version}\n")

        timeStamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        mainStartTime = time.time()

        self.holderAttribute = self.parameterAsString(parameters, 'AssignedByField', context)

        filePath = self.parameterAsVectorLayer(parameters, 'Inputlayer', context).source()
        directory = os.path.dirname(filePath)
        parameters["OutputDirectory"] = directory
        if filePath[-4:].lower() != 'gpkg' and os.path.splitext(filePath)[1][:5].lower() != '.gpkg':
            feedback.reportError('The layer is not part of a GPKG')
            return {}
        else:
            try:
                gpkg_path, source_layer_name = filePath.split("|layername=")
            except ValueError:
                gpkg_path = filePath
                source_layer_name = vgle_gpkgs.getFirstLayerFromGPKG(gpkg_path)
                if not source_layer_name:
                    feedback.reportError("No feature layers found in the GPKG.")
                    return None
            self.layer = (gpkg_path, source_layer_name)
       
        vgle_utils.startLogging(self.parameterAsVectorLayer(parameters, 'Inputlayer', context), parameters, timeStamp, self.version)

        csv_path = os.path.join(directory, f'{self.SwapFreqLayer}_groups.csv')
        csv_sanitized = csv_path.replace('\\', '/')

        if parameters['ralgorithm'] == 1:
            result = processing.run("r:topdown2", {
                'INPUT': frequency,
                'OBJECTIVE_FUNCTION': parameters['robjective'],
                'RESOLUTION': parameters['resolution'],
                'Group': csv_sanitized
            }, context=context, feedback=feedback)
        else:
            result = processing.run("r:topdown", {
                'INPUT': frequency,
                'Group': csv_sanitized
            }, context=context, feedback=feedback)

        groupsCSV = result['Group']

        feedback.pushInfo('Group declaration started')
        parts = str(Path(groupsCSV).stem).split('_')

        feedback.pushInfo('Group CSV created at: ' + groupsCSV)
        uri = f"file:{groupsCSV}?type=csv&geomType=none"
        csvLayer = QgsVectorLayer(uri, "csv_no_geom", "delimitedtext")
        groups = {}
        assigned_holders = set()
        for f in csvLayer.getFeatures():
            holder_id = f[0] 
            group_id  = f[1] 
            groups.setdefault(group_id, []).append(holder_id)
            assigned_holders.add(holder_id)
        none_group = max(groups.keys())
        none_group_members = queryNoneGroupMembers(self, assigned_holders)
        
        if none_group_members:
            groups[none_group + 1] = none_group_members
        #feedback.setSteps(len(groups) + 1)
        feedback.setCurrentStep(1)
        feedback.pushInfo('Group declaration finished!')

        feedback.pushInfo('Group processing started!')
        topdownGroupsLayer = None
        results['OUTPUT'] = []
        group_paths = {}
        counter = 1
        self.colList = [self.parameterAsString(parameters, 'AssignedByField', context), self.parameterAsString(parameters, 'BalancedByField', context)]
        for key, group in groups.items():
            counter += 1
            group_table, group_rows = self.selectGroup(timeStamp, key, group)
            if topdownGroupsLayer is None:
                topdownGroupsLayer = self.createEmptyGroup(timeStamp, key, 'topdown_groups', group)
                topdownGroupsLayerMerged = self.createEmptyGroup(timeStamp, key, 'topdown_groups_merged', group)
            feedback.pushInfo(f'Group {key} processing started with {group_rows} features')
            tempResult = processing.run("Polygon Grouper:polygon_grouper_gpkg", {
                    'Inputlayer': QgsVectorLayer(f'{gpkg_path}|layername={group_table}', f"group_{key}", "ogr"),
                    'Preference': parameters['Preference'],
                    'AssignedByField': [parameters['AssignedByField']],
                    'BalancedByField': parameters['BalancedByField'],
                    'Tolerance': parameters['Tolerance'],
                    'DistanceThreshold': parameters['DistanceThreshold'],
                    'SwapToGet': parameters['SwapToGet'],
                    'OutputDirectory': parameters['OutputDirectory'],
                    'OnlySelected': parameters['OnlySelected'],
                    'Single': parameters['Single'],
                    'StrictHDI': parameters['StrictHDI'],
                    'StrictHFI': parameters['StrictHFI'],
                    'Simply': parameters['Simply'],
                    'Stats': False,
                    'IS_CHILD': True
                }, context=context, feedback=feedback, is_child_algorithm=True)
            feedback.setCurrentStep(counter)
            feedback.pushInfo(f'Group {key} processing finished!')
            group_paths[key] = (tempResult['OUTPUT'], tempResult['MERGED'])
            extendLayerWithGroup(self, topdownGroupsLayer, tempResult['OUTPUT'], context)
            extendLayerWithGroup(self, topdownGroupsLayerMerged, tempResult['MERGED'], context)
            vgle_gpkgs.deleteTable(gpkg_path, group_table)


        for swapped_uri, merged_uri in group_paths.values():
            gpkg, swapped = swapped_uri.split("|layername=")
            vgle_gpkgs.deleteTable(gpkg, swapped)
            gpkg, merged = merged_uri.split("|layername=")
            vgle_gpkgs.deleteTable(gpkg, merged)

        
        cleaned_name = topdownGroupsLayer.replace('"', '').replace("'", '').strip()
        uri = f'{gpkg_path}|layername={cleaned_name}'
        feedback.pushInfo(f"URI: {uri}")
        context.addLayerToLoadOnCompletion(
            uri,
            QgsProcessingContext.LayerDetails(topdownGroupsLayer, context.project())
        )

        cleaned_name = topdownGroupsLayerMerged.replace('"', '').replace("'", '').strip()
        uri = f'{gpkg_path}|layername={cleaned_name}'
        feedback.pushInfo(f"URI: {uri}")
        context.addLayerToLoadOnCompletion(
            uri,
            QgsProcessingContext.LayerDetails(topdownGroupsLayerMerged, context.project())
        )

        results['OUTPUT'] = topdownGroupsLayer
        results['MERGED'] = topdownGroupsLayerMerged

        return results

    def selectGroup(self, timeStamp, postfix, fids):
        gpkg_path, source_table = self.layer
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()

        id_list_str = ",".join(map(str, fids))
        placeholders = ",".join(["?"] * len(fids))

        target_table = f"{source_table}_{timeStamp}_group_{postfix}"
        
        try:
            cur.execute("BEGIN TRANSACTION;")

            cur.execute(f"SELECT column_name FROM gpkg_geometry_columns WHERE table_name = '{source_table}';")

            geom_col = cur.fetchone()[0]

            cur.execute(f'CREATE TABLE "{target_table}" AS SELECT {self.colList[0]}, {self.colList[1]}, {geom_col} FROM "{source_table}" WHERE 1=0;')

            query = f"""
                INSERT INTO "{target_table}" ({self.colList[0]}, {self.colList[1]}, {geom_col}) 
                SELECT {self.colList[0]}, {self.colList[1]}, {geom_col} FROM "{source_table}" 
                WHERE "{self.holderAttribute}" IN ({placeholders})
            """
            cur.execute(query, fids)

            cur.execute(f'ALTER TABLE "{target_table}" ADD COLUMN topdown_group REAL;')

            conn.commit()

            cur.execute(f'UPDATE "{target_table}" SET topdown_group = {postfix};')

            cur.execute(f"""
                INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change, min_x, min_y, max_x, max_y, srs_id)
                SELECT "{target_table}", data_type, "{target_table}", description, datetime('now'), 
                    min_x, min_y, max_x, max_y, srs_id
                FROM gpkg_contents WHERE table_name = "{source_table}"
            """)

            cur.execute(f"""
                INSERT INTO gpkg_geometry_columns (table_name, column_name, geometry_type_name, srs_id, z, m)
                SELECT "{target_table}", column_name, geometry_type_name, srs_id, z, m
                FROM gpkg_geometry_columns WHERE table_name = "{source_table}"
            """)

            conn.commit()

            cur.execute(f'SELECT * FROM "{target_table}";')
            rows = len(cur.fetchall())

            conn.commit()
            conn.close()
            return target_table, rows

        except Exception as e:
            conn.rollback()
            print(f"Error creating subset: {e}")
            return False, False
        finally:
            conn.close()

    def createEmptyGroup(self, timeStamp, key, postfix, fids):
        gpkg_path, source_table = self.layer
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()

        id_list_str = ",".join(map(str, fids))
        placeholders = ",".join(["?"] * len(fids))

        target_table = f"{source_table}_{timeStamp}_group_{postfix}"
        
        try:
            cur.execute("BEGIN TRANSACTION;")

            cur.execute(f"SELECT column_name FROM gpkg_geometry_columns WHERE table_name = '{source_table}';")

            geom_col = cur.fetchone()[0]

            cur.execute(f'CREATE TABLE "{target_table}" AS SELECT {self.colList[0]}, {self.colList[1]}, {geom_col} FROM "{source_table}" WHERE 1=0;')

            query = f"""
                INSERT INTO "{target_table}" ({self.colList[0]}, {self.colList[1]}, {geom_col}) 
                SELECT {self.colList[0]}, {self.colList[1]}, {geom_col} FROM "{source_table}" 
                WHERE "{self.holderAttribute}" IN ({placeholders})
            """
            cur.execute(query, fids)

            cur.execute(f'ALTER TABLE "{target_table}" ADD COLUMN topdown_group REAL;')

            conn.commit()

            cur.execute(f'UPDATE "{target_table}" SET topdown_group = {key};')

            cur.execute(f"""
                INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change, min_x, min_y, max_x, max_y, srs_id)
                SELECT "{target_table}", data_type, "{target_table}", description, datetime('now'), 
                    min_x, min_y, max_x, max_y, srs_id
                FROM gpkg_contents WHERE table_name = "{source_table}"
            """)

            cur.execute(f"""
                INSERT INTO gpkg_geometry_columns (table_name, column_name, geometry_type_name, srs_id, z, m)
                SELECT "{target_table}", column_name, geometry_type_name, srs_id, z, m
                FROM gpkg_geometry_columns WHERE table_name = "{source_table}"
            """)

            conn.commit()

            conn.close()
            return target_table

        except Exception as e:
            conn.rollback()
            print(f"Error creating subset: {e}")
            return False, False
        finally:
            conn.close()

    def selectGroupAllCols(self, timeStamp, postfix, fids):
        gpkg_path, source_table = self.layer
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()

        id_list_str = ",".join(map(str, fids))
        placeholders = ",".join(["?"] * len(fids))

        target_table = f"{source_table}_{timeStamp}_group_{postfix}"
        
        try:
            cur.execute("BEGIN TRANSACTION;")

            cur.execute(f'PRAGMA table_info("{source_table}")')
            columns = [f'"{col[1]}"' for col in cur.fetchall() if col[1].lower() != 'fid']
            column_string = ", ".join(columns)

            cur.execute(f'CREATE TABLE "{target_table}" AS SELECT * FROM "{source_table}" WHERE 1=0;')

            cur.execute(f'ALTER TABLE "{target_table}" ADD COLUMN "topdown_group" REAL;')

            query = f"""
                INSERT INTO "{target_table}" ({column_string}) 
                SELECT {column_string} FROM "{source_table}" 
                WHERE "{self.holderAttribute}" IN ({placeholders})
            """
            cur.execute(query, fids)

            cur.execute(f'UPDATE "{target_table}" SET "topdown_group" = {postfix};')

            cur.execute(f"""
                INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change, min_x, min_y, max_x, max_y, srs_id)
                SELECT "{target_table}", data_type, "{target_table}", description, datetime('now'), 
                    min_x, min_y, max_x, max_y, srs_id
                FROM gpkg_contents WHERE table_name = "{source_table}"
            """)

            cur.execute(f"""
                INSERT INTO gpkg_geometry_columns (table_name, column_name, geometry_type_name, srs_id, z, m)
                SELECT "{target_table}", column_name, geometry_type_name, srs_id, z, m
                FROM gpkg_geometry_columns WHERE table_name = "{source_table}"
            """)

            cur.execute(f"SELECT * FROM {target_table};")
            rows = len(cur.fetchall())

            conn.commit()
            conn.close()
            return target_table, rows

        except Exception as e:
            conn.rollback()
            print(f"Error creating subset: {e}")
            return False, False
        finally:
            conn.close()


    def add_groups(self, geopackage, results, context, keyword=None):
        vlayer = QgsVectorLayer(geopackage, "test", "ogr")
        sublayers = vlayer.dataProvider().subLayers()
        layer_names = []

        for sublayer in sublayers:
            layer_name = sublayer.split("!!")[2]
            layer_names.append(layer_name)

        for layer_name in layer_names:
            uri = f"{geopackage}|layername={layer_name}"
            vlayer = QgsVectorLayer(uri, layer_name, "ogr")
            context.temporaryLayerStore().addMapLayer(vlayer)
            results[keyword].append(vlayer)
            context.addLayerToLoadOnCompletion(
            vlayer.id(), 
            QgsProcessingContext.LayerDetails(vlayer.name(), context.project(), keyword)
            )

    #def postProcessAlgorithm(self, context, feedback):
    #    delete_shapefiles(self.locked_files)
    #    return {}


def is_r_provider_installed():
    registry = QgsApplication.processingRegistry()
    providers = [p.id() for p in registry.providers()]
    if "r" in providers or "processing_r" in providers:
        return True
    else:
        return enable_r_plugin()

def enable_r_plugin(reload=False):
    if reload:
        try:
            qgis.utils.unloadPlugin("processing_r")
            qgis.utils.loadPlugin("processing_r")
            qgis.utils.startPlugin("processing_r")
            return True
        except Exception as e:  
            return False

    try:
        if "processing_r" not in qgis.utils.plugins:
            qgis.utils.loadPlugin("processing_r")
            qgis.utils.startPlugin("processing_r")
            return True
    except Exception as e:
        return False

def checkR_folder():
    settings = QgsSettings()
    r_folder = settings.value("Processing/Configuration/R_FOLDER")

    if not r_folder:
        r_folder = ProcessingConfig.getSetting('R_FOLDER')
        if not r_folder:
            return False

    r_folder = str(r_folder)

    if not r_folder or not os.path.exists(r_folder):
        return False
    return r_folder

def copyR_script(r_script_path):
    settings = QgsSettings()
    dest_folder = settings.value("Processing/Configuration/R_SCRIPTS_FOLDER")
    if not dest_folder:
        dest_folder = ProcessingConfig.getSetting('R_SCRIPTS_FOLDER')

    if dest_folder:
        dest_path = os.path.join(dest_folder, os.path.basename(r_script_path))
        try:
            if not os.path.isfile(dest_path): 
                shutil.copy(r_script_path, dest_path)
            else:
                os.remove(dest_path)
                shutil.copy(r_script_path, dest_path)
            if not enable_r_plugin(reload=True):
                raise Exception("Failed to enable R plugin after copying RSX script.")
        except Exception as e:
            return False
    else:
        try:
            settings_dir = QgsApplication.qgisSettingsDirPath()
            rsx_cache_path = os.path.join(settings_dir, "processing", "rscripts", os.path.basename(r_script_path))
            if not os.path.isfile(rsx_cache_path): 
                shutil.copy(r_script_path, rsx_cache_path)
            else:
                os.remove(rsx_cache_path)
                shutil.copy(r_script_path, rsx_cache_path)
            if not enable_r_plugin(reload=True):
                raise Exception("Failed to enable R plugin after copying RSX script.")
        except Exception as e:
            return False   
    return True

def extendLayerWithGroup(self, layer, group_layer_path, context):
    gpkg_path, source_table = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    group_layer_name = group_layer_path.split("|layername=")[1]
        
    try:
        cur.execute(f'PRAGMA table_info("{layer}")')
        source_columns = [col[1] for col in cur.fetchall()]

        cur.execute(f'PRAGMA table_info("{group_layer_name}")')
        groups_data = cur.fetchall()
        for col in groups_data:
            col_name = col[1]
            col_type = col[2]
            if col_name.lower() != 'fid' and col_name not in source_columns:
                cur.execute(f'ALTER TABLE "{layer}" ADD COLUMN "{col_name}" {col_type}')

        cur.execute(f'PRAGMA table_info("{layer}")')
        final_target_cols = [f'"{col[1]}"' for col in cur.fetchall() if col[1].lower() != 'fid']
        
        col_string = ", ".join(final_target_cols)

        cur.execute(f"""
            INSERT INTO "{layer}" ({col_string}) 
            SELECT {col_string} FROM "{group_layer_name}"
        """)

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        conn.rollback()
        print(f"Error updating groupLayer: {e}")
        return False
    finally:
        conn.close()

def queryNoneGroupMembers(self, assigned_holders):
    gpkg_path, source_table = self.layer
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()

    try:
        id_list_str = ",".join(map(str, assigned_holders)) if assigned_holders else "NULL"
        placeholders = ",".join(["?"] * len(assigned_holders)) if assigned_holders else "NULL"
        cur.execute(f"""
            SELECT "{self.holderAttribute}" FROM "{source_table}"
            WHERE "{self.holderAttribute}" NOT IN ({id_list_str})
        """)
        return [row[0] for row in cur.fetchall()]

    except Exception as e:
        print(f"Error querying none group members: {e}")
        return []
    finally:
        conn.close()



