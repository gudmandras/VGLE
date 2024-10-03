__author__ = 'GOPA'
__date__ = '2024-09-05'
__copyright__ = '(C) 2024 by GOPA'
__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (Qgis,
                        QgsProject,
                        QgsProcessing, 
                        QgsProcessingAlgorithm, 
                        QgsProcessingMultiStepFeedback, 
                        QgsProcessingParameterBoolean,
                        QgsProcessingParameterVectorLayer,
                        QgsProcessingParameterNumber,
                        QgsProcessingParameterFile,
                        QgsProcessingParameterEnum,
                        QgsProcessingParameterField,
                        QgsProcessingFeatureSourceDefinition,
                        QgsLayerTree, 
                        QgsField, 
                        QgsVectorFileWriter, 
                        QgsVectorLayer,
                        QgsFeatureRequest, 
                        QgsExpression)
import processing
import qgis.core
import os.path
from datetime import datetime
import time, copy, uuid, logging, itertools

class Polygon_grouper(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        import ptvsd
        ptvsd.debug_this_thread()
        self.addParameter(QgsProcessingParameterVectorLayer('Inputlayer', 'Input layer', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterBoolean('Preference', 'Give preference for the selected features', defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean('Onlyselected', 'Only use the selected features', defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean('Single', "Use single holding's holders polygons", defaultValue=False))
        self.addParameter(QgsProcessingParameterField('Assignedbyfield', 'Assigned by field', type=QgsProcessingParameterField.Any, parentLayerParameterName='Inputlayer', allowMultiple=True, defaultValue=''))
        self.addParameter(QgsProcessingParameterField('Balancedbyfield', 'Balanced by field', type=QgsProcessingParameterField.Numeric, parentLayerParameterName='Inputlayer', allowMultiple=False, defaultValue=''))
        self.addParameter(QgsProcessingParameterNumber('Tolerance', 'Tolerance (%)', type=QgsProcessingParameterNumber.Integer, minValue=5, maxValue=100, defaultValue=5))
        self.addParameter(QgsProcessingParameterNumber('Distancetreshold', 'Distance treshold (m)', type=QgsProcessingParameterNumber.Integer, minValue=0, defaultValue=1000))
        self.addParameter(QgsProcessingParameterEnum('Swaptoget', 'Swap to get', options=['Neighbours','Closer','Neighbours, then closer','Closer, then neighbours'], allowMultiple=False, defaultValue='Neighbours'))
        self.addParameter(QgsProcessingParameterFile('Outputdirectory', 'Output directory', behavior=QgsProcessingParameterFile.Folder, fileFilter='Minden fájl (*.*)', defaultValue=None))
        self.algorithm_names = ['Neighbours', 'Closer', "Neighbours, then closer", "Closer, then neighbours"]

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
        return """<html><body><h2>Algoritm description</h2>
        <p>Algorithm for spatialy grouping polygons which have the same value in certain field. The spatial grouping balanced by balanced by field parameter and threshold. Besides, the range of the grouping can be decrease with the distance threshold.</p>
        <h2>Input parameters</h2>
        <h3>Input layer</h3>
        <p>Vector layer with polygon geometries.</p>
        <h3>Give preference for the selected features</h3>
        <p>The selected features in the input layer will be used as seed polygons, and the grouping will be around these features. Without these, the seed polygons are the largest polygons per the assigned by field unique values.</p>
        <h3>Only use the selected features</h3>
        <p>Only works, if the "Give preference for the selected features" parameter is True. If checked, only the selected polygons will used, else the selected and the largest polygons together.</p>
        <h3>Use single holding's holders polygons</h3>
        <p>Use holder's polygons, which have only one polygon.</p>
        <h3>Assigned by field</h3>
        <p>The field which contains holder of different number of features inside the input layer.</p>
        <h3>Balanced by field</h3>
        <p>The value which applied for the assigned by field's unique values polygons. Recommended an area field.</p>
        <h3>Distance treshold</h3>
        <p>The distance range within the grouping for a certain polygon happens. Distance in meter.</p>
        <h3>Tolerance</h3>
        <p>The percent for the balance field.</p>
        <h3>Swap to get</h3>
        <p>The method, with the grouping will be happen.
        Neighbours: Change the neighbours of the seed polygons.
        Closer: Change to get closer the other polygons to the seed polygon.
        Neighbours, then closer: Combinated run, first Neighbours, than Closer function will be run on the results of the Neighbours.
        Closer, then neighbours: Combinated run, first Closer, than Neighbours function will be run on the results of the Closer.</p>
        <h3>Output directory</h3>
        <p>The directory where the outputs will saved. Two output layer will be created, with timestamp in their names:
        First: Vector layer, with the base layer name + algorithm name + timestamp
        Second: Merged layer, a dissolved vector layer based on the final state of the grouping. (Because of the dissolving, only the last field is valid in this layer)</p>
        <br></body></html>"""

    def createInstance(self):
        return Polygon_grouper()

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        import ptvsd
        ptvsd.debug_this_thread()
        results = {}
        self.steps = self.calculate_steps(parameters['Swaptoget'])
        feedback = QgsProcessingMultiStepFeedback(self.steps, model_feedback)

        if parameters['Onlyselected'] and parameters['Preference'] is not True:
            feedback.pushInfo(f"'Only use the selected features' parameters works only with 'Give preference for the selected features parameter'. This parameter is invalided") 
        
        timestamp = datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y_%H_%M_%S")
        main_start_time = time.time()

        #Get inputs
        self.weight = parameters['Balancedbyfield']
        self.tolerance = parameters['Tolerance']
        self.distance = parameters['Distancetreshold']
        self.use_single = parameters['Single']
        self.algorithm_index = parameters['Swaptoget']
        self.algorithm_name = self.algorithm_names[self.algorithm_index].lower()

        input_layer = self.parameterAsVectorLayer(parameters, 'Inputlayer', context)
       
        self.start_logging(input_layer, parameters, timestamp)

        temp_layer = self.create_temp_layer(input_layer, parameters["Outputdirectory"], self.algorithm_name, timestamp)
        swaped_layer = None
        merged_layer = None

        layer, self.holder_attribute = self.set_holder_field(temp_layer, parameters["Assignedbyfield"])
        self.holder_attr_type, self.holder_attr_len = self.get_field_props(temp_layer, self.holder_attribute)
        holders_with_holdings = self.get_holders_holdings(layer)
        layer, self.id_attribute, holders_with_holdings = self.create_id_field(layer, holders_with_holdings)
        holdings_with_area = self.get_holdings_areas(layer, parameters["Balancedbyfield"])
        self.hol_w_hol = holders_with_holdings
        self.hol_w_aea = holdings_with_area
        self.holder_total_area = self.calculate_total_area()

        if parameters['Preference']:
            selected_features = self.get_selected_features(input_layer)
            self.determine_seed_polygons(layer, parameters['Preference'], parameters['Onlyselected'], selected_features)
        else:
            self.determine_seed_polygons(layer)

        feedback.pushInfo('Calculate distance matrix')
        self.distance_matrix, attr_names = self.create_distance_matrix(layer)
        feedback.pushInfo('Distance matrix calculated')
        self.filtered_distances = self.filter_distance_matrix()

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            self.end_logging()
            return {}
       
        if self.algorithm_index == 0:
            swaped_layer = self.neighbours(layer, feedback)
        elif self.algorithm_index == 1:
            one_seed_bool = self.check_seed_number(feedback)
            if one_seed_bool:
                swaped_layer = self.closer(layer, feedback)
            else:
                swaped_layer = False
        elif self.algorithm_index == 2:
            one_seed_bool = self.check_seed_number(feedback)
            if one_seed_bool:
                original_seeds = copy.deepcopy(self.seeds)
                swaped_layer = self.neighbours(layer, feedback)
                swaped_layer = self.closer(swaped_layer, feedback, original_seeds)
            else:
                swaped_layer = False
        elif self.algorithm_index == 3:
            one_seed_bool = self.check_seed_number(feedback)
            if one_seed_bool:
                swaped_layer = self.closer(layer, feedback)
                swaped_layer = self.neighbours(swaped_layer, feedback)
            else:
                swaped_layer = False
        
        if swaped_layer:
            feedback.setCurrentStep(self.steps-1)

            QgsProject.instance().addMapLayer(swaped_layer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, swaped_layer)
            swaped_layer.commitChanges()

            merged_layer = self.create_merged_file(swaped_layer, parameters["Outputdirectory"], timestamp)

            QgsProject.instance().addMapLayer(merged_layer, False)
            root = QgsProject().instance().layerTreeRoot()
            root.insertLayer(0, merged_layer)

            main_end_time = time.time()
            logging.debug(f'Script time:{main_end_time-main_start_time}')

            feedback.setCurrentStep(self.steps)
            self.end_logging()   
            results['OUTPUT'] = swaped_layer
            return results
        else:
            self.end_logging()   
            return {}

    def start_logging(self, layer, parameters, timestamp):
        path = os.path.join(parameters["Outputdirectory"], f"{str(layer.name())}_log_{timestamp}.txt")
        formatter = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(filename=path, level=logging.DEBUG, format=formatter, filemode='w')
        logging.debug(f'Start time: {datetime.now().strftime("%Y_%m_%d_%H_%M")}\nInput layer: {parameters["Inputlayer"]}\nPreference to selected items: {parameters["Preference"]}\nUse single holdings holders polygons: {parameters["Single"]}\nHolder atrribute(s): {parameters["Assignedbyfield"]}\nWeight attribute: {parameters["Balancedbyfield"]}\nTolerance threshold: {parameters["Tolerance"]}\nDistance threshold: {parameters["Distancetreshold"]}\nOutput dir: {parameters["Outputdirectory"]}')

    def end_logging(self):
        logger = logging.getLogger() 
        handlers = logger.handlers[:]
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        logging.shutdown()

    def calculate_steps(self, algorithm_index):
        if algorithm_index == 0:
            return 13
        elif algorithm_index == 1:
            return 18
        else:
            return 28 

    def get_field_props(self, layer, field_name):
        for field in layer.fields():
            if field.name() == field_name:
                return field.type(), field.length()

    def get_selected_features(self, input_layer):
        alg_params = {
            'INPUT': input_layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        selected_features = processing.run("native:saveselectedfeatures",alg_params)["OUTPUT"]
        return selected_features

    def create_temp_layer(self, layer, directory, postfix, timestamp=None):
        if directory:
            if timestamp:
                path = os.path.join(directory, f"{str(layer.name())}_{postfix}_{timestamp}.shp")
            else:
                path = os.path.join(directory, f"{str(layer.name())}_{postfix}.shp")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "ESRI Shapefile"
            options.fileEncoding = "UTF-8"
            context = QgsProject.instance().transformContext()
            QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, context, options)
            temp_layer = QgsVectorLayer(path, f"{postfix} layer", "ogr")
            return temp_layer
        else:
            epsg = layer.crs().geographicCrsAuthId()[-4:]
            feats = [feature for feature in layer.getFeatures()]
            mem_layer = QgsVectorLayer(f"Polygon?crs=epsg:{epsg}", f"{postfix} layer", "memory")
            mem_layer_data = mem_layer.dataProvider()
            attr = layer.dataProvider().fields().toList()
            mem_layer_data.addAttributes(attr)
            mem_layer.updateFields()
            mem_layer_data.addFeatures(feats)
            mem_layer.commitChanges()
            return mem_layer

    def check_seed_number(self, feedback):
        for seed in self.seeds.values():
            if len(seed) > 1:
                feedback.pushInfo('More than one feature preference for one holder at closer function - algorithm stop') 
                return False
        return True

    def set_holder_field(self, layer, field):
        if len(field) == 1:
            return layer, field[0]
        else:
            layer, field_name = self.set_temp_holder_field(layer)
            layer = self.set_temp_holder_value(layer, field_name, field)
            return layer, field_name

    def set_temp_holder_field(self, layer):
        field_name = 'holder_id'
        layer_attributes = self.get_attributes_names(layer)
        if field_name in layer_attributes:
            counter = 0
            while field_name in layer_attributes:
                field_name = f"{field_name}{counter}"
                counter += 1
        layer.startEditing()
        data_provider = layer.dataProvider()
        data_provider.addAttributes([QgsField(field_name, QVariant.Int)])
        self.holder_attr_type = QVariant.Int
        self.holder_attr_len = -1
        layer.updateFields()
        return layer, field_name

    def get_attributes_names(self, layer):
        attributes = [field.name() for field in layer.fields()]
        return attributes

    def set_temp_holder_value(self, layer, field_name, attributes):
        field_name_id = [turn for turn, field in enumerate(layer.fields()) if field.name() == field_name][0]
        big_list = []
        features = layer.getFeatures()
        for turn, feature in enumerate(features):
            tempList = []
            for attr in attributes:
                value = feature.attribute(attr)
                if value != qgis.core.NULL:
                    tempList.append(value)
            temp_string = ''
            for turn, tmp in enumerate(tempList):
                if len(tempList) > 1:
                    if tmp != '' and tmp  != qgis.core.NULL:
                        if tmp != tempList[-1]:
                            temp_string += f'{tmp},'
                        else:
                            temp_string += f'{tmp}'
                        if len(temp_string) > 0:
                            if temp_string not in big_list:
                                big_list.append(temp_string)
                else:
                    if tmp and tmp != '' and tmp  != qgis.core.NULL:
                        if temp_string not in big_list:
                            temp_string += f'{tmp}'
        counter = 1
        for lista in big_list:
            expression = ''
            list_unique = lista.split(',')
            for turn, unique in enumerate(list_unique):
                if unique != qgis.core.NULL:
                    if type(unique) == str:
                        if turn+1 != len(list_unique):
                            expression += '"{field}"=\'{value}\' AND '.format(field=attributes[turn],value=str(unique))
                        else:
                            expression += '"{field}"=\'{value}\''.format(field=attributes[turn],value=str(unique))
                    else:
                        if turn+1 != len(list_unique):
                            expression += '"{field}"={value} AND '.format(field=attributes[turn],value=str(unique))
                        else:
                            expression += '"{field}"={value}'.format(field=attributes[turn],value=str(unique))
            layer.selectByExpression(expression)
            selected_features = layer.selectedFeatures()
            if layer.selectedFeatureCount() > 0:
                counter += 1
                if (layer.isEditable() == False):
                    layer.startEditing()
                for feature in selected_features:
                    layer.changeAttributeValue(feature.id(),field_name_id,counter)
                layer.removeSelection()
        layer.commitChanges()

        return layer

    def create_id_field(self, layer, holders):
        field_name = 'temp_id'
        layer_attributes = self.get_attributes_names(layer)
        if field_name in layer_attributes:
            counter = 0
            while field_name in layer_attributes:
                field_name = f"{field_name}{counter}"
                counter += 1
        if (layer.isEditable() == False):
            layer.startEditing()
        data_provider = layer.dataProvider()
        data_provider.addAttributes([QgsField(field_name, QVariant.String, len=10)])
        layer.updateFields()
        layer, holders_with_holding_id = self.set_id_field(layer, field_name, holders)
        return layer, field_name, holders_with_holding_id

    def set_id_field(self, layer, attribute, holders):
        attribute_id = self.get_attributes_names(layer).index(attribute)
        holders_with_holding_id = {}
        if (layer.isEditable() == False):
            layer.startEditing()
        for holder, holdings in holders.items():
            counter = 0
            for feature_id in holdings:
                new_id = str(uuid.uuid4())[:10]
                layer.changeAttributeValue(feature_id,attribute_id,new_id)
                counter += 1
                if holder in list(holders_with_holding_id.keys()):
                    holders_with_holding_id[holder].append(new_id)
                else:
                    holders_with_holding_id[holder] = list()
                    holders_with_holding_id[holder].append(new_id)
        layer.commitChanges()
        return layer, holders_with_holding_id

    def get_holders_holdings(self, layer):
        holders_with_holdings = {}
        features = layer.getFeatures()
        for feature in features:
            feature_id = feature.id()
            holder = feature.attribute(self.holder_attribute)
            if holder != qgis.core.NULL:
                if holder in list(holders_with_holdings.keys()):
                    holders_with_holdings[holder].append(feature_id)
                else:
                    holders_with_holdings[holder] = [feature_id]
        return holders_with_holdings

    def calculate_total_area(self):
        holder_total_area = {}
        for holder, holdings in self.hol_w_hol.items():
            total_area = 0
            for holding in holdings:
                if self.hol_w_aea[holding] != qgis.core.NULL:
                    total_area += self.hol_w_aea[holding]
            holder_total_area[holder] = total_area
        return holder_total_area

    def get_holdings_areas(self, layer, area_id):
        holdings_with_areas = {}
        features = layer.getFeatures()
        for feature in features:
            area = feature.attribute(area_id)
            if area == qgis.core.NULL:
                area = feature.geometry().area()/10000
            holding_id = feature.attribute(self.id_attribute)
            holdings_with_areas[holding_id] = area
        return holdings_with_areas

    def determine_seed_polygons(self, layer, preference=False, only_selected=False, selected_features=None):
        holders_with_seeds = {}
        if preference:
            alg_params = {
                'INPUT': layer,
                'PREDICATE':[3],
                'INTERSECT':selected_features,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            processing.run("native:selectbylocation", alg_params)['OUTPUT']
            selfeatures = layer.selectedFeatures()
            for feature in selfeatures:
                holder_value = feature.attribute(self.holder_attribute)
                id_value = feature.attribute(self.id_attribute)
                if holder_value not in list(holders_with_seeds.keys()):
                    holders_with_seeds[holder_value] = [id_value]
                else:
                    holders_with_seeds[holder_value].append(id_value)
        for holder, holdings in self.hol_w_hol.items():
            if only_selected:
                if holder not in list(holders_with_seeds.keys()):
                    holders_with_seeds[holder] = []
            else:
                if holder not in list(holders_with_seeds.keys()):
                    if holder == 'NULL':
                        holders_with_seeds[holder] = holdings
                    else:
                        if self.use_single:
                            if len(holdings) > 1:
                                largest_area = 0
                                largest_feature_id = ''
                                for holding in holdings:
                                    area_value = self.hol_w_aea[holding]
                                    if largest_area < area_value:
                                        largest_area = area_value
                                        largest_feature_id = holding
                                holders_with_seeds[holder] = [largest_feature_id]
                            else:
                                holders_with_seeds[holder] = []
                        else: 
                            largest_area = 0
                            largest_feature_id = ''
                            for holding in holdings:
                                area_value = self.hol_w_aea[holding]
                                if largest_area < area_value:
                                    largest_area = area_value
                                    largest_feature_id = holding
                            holders_with_seeds[holder] = [largest_feature_id]
        
        self.seeds = holders_with_seeds

    def get_changable_holdings(self, in_distance=None):
        changable_holdings = []
        if in_distance:
            for holding in in_distance:
                seed_listed = [seed for seed_list in list(self.seeds.values()) for seed in seed_list]
                if holding not in seed_listed:
                    changable_holdings.append(holding)
        else:
            for holder, holdings in self.hol_w_hol.items():
                for holding in holdings:
                    if holding not in self.seeds[holder]:
                        changable_holdings.append(holding)
        return changable_holdings

    def get_neighbours(self, layer, seed):
        expression = f'"{self.id_attribute}" = \'{seed}\''
        layer.selectByExpression(expression)
        alg_params = {
            'INPUT': layer,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        seed_features = processing.run('native:saveselectedfeatures', alg_params)["OUTPUT"]
        alg_params = {
            'INPUT': layer,
            'INTERSECT': seed_features,
            'PREDICATE': 4,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        neighbours = processing.run('native:extractbylocation', alg_params)["OUTPUT"]
        layer.removeSelection()

        ngh_features = neighbours.getFeatures()
        nghs_ids = [x.attribute(self.id_attribute) for x in ngh_features]

        return nghs_ids, neighbours

    def distance_search(self, seed):
        search_items = []
        distances = self.distance_matrix[seed]
        sorted_distances = [(y, x) for y, x in zip(list(distances.values()),list(distances.keys())) if y != seed and x != seed]
        sorted_distances.sort()
        for value, key in sorted_distances:
            if value <= self.distance:
                search_items.append(key)
            else:
                break
        return search_items

    def ids_for_change(self, holding_list, changables):
        try:
            ids = []
            for h_id in holding_list:
                if h_id in changables:
                    ids.append(h_id)
            return ids
        except ValueError:
            return None

    def neighbours(self, layer, feedback):
        self.counter = 0
        changes = 1
        changer = True
        self.global_changables = self.get_changable_holdings()

        try:
            turn = int(self.nholder_attribute.split('_')[0])
            if self.algorithm_index == 3:
                if turn == (self.steps/2)-3:
                    self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])) + self.nholder_attribute[2:]
                    self.nid_attribute = str(int(self.nid_attribute.split('_')[0])) + self.nid_attribute[2:]
                else:
                    if turn >= 10:
                        self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])-1) + self.nholder_attribute[2:]
                        self.nid_attribute = str(int(self.nid_attribute.split('_')[0])-1) + self.nid_attribute[2:]
                    else:
                        self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])-1) + self.nholder_attribute[1:]
                        self.nid_attribute = str(int(self.nid_attribute.split('_')[0])-1) + self.nid_attribute[1:]
        except AttributeError:
            turn = 0

        local_total_areas = copy.deepcopy(self.holder_total_area)
        feedback.pushInfo(f'Neighbours algorithm start') 

        while changer:
            turn += 1
            layer = self.set_turn_attributes(layer, turn)
            changables = []
            feedback.pushInfo(f'Turn {turn}')  
            for holder, holdings in self.hol_w_hol.items():
                if holder != 'NULL':
                    seeds = self.seeds[holder]
                    if len(seeds) >= 1:
                        for seed in seeds:
                            ngh_ids, neighbours_layer = self.get_neighbours(layer, seed)
                            in_distance = self.filtered_distances[seed]
                            distance_changes = self.get_changable_holdings(in_distance)
                            local_changables = [dist for dist in distance_changes if dist in self.global_changables and dist not in changables]
                            if len(local_changables) > 0:
                                holdings_ids = []
                                changes_ids = []
                                
                                for h_id in holdings:
                                    if h_id in ngh_ids:
                                        if h_id not in self.seeds[holder]:
                                            self.seeds[holder].append(h_id)
                                    else:
                                        holdings_ids.append(h_id)

                                ngh_features = neighbours_layer.getFeatures()
                                for nghfeat in ngh_features:
                                    # Get holder total area
                                    holder_total_area = local_total_areas[holder]
                                    #Filter holdings
                                    filtered_holdings_ids = self.ids_for_change(holdings_ids, local_changables)
                                    if holdings_ids:
                                        # Get ngh holder name
                                        ngh_holder = nghfeat.attribute(self.nholder_attribute)
                                        if ngh_holder != 'NULL' and ngh_holder != holder:
                                            # Get holder total area
                                            ngh_holder_total_area = local_total_areas[ngh_holder]
                                            # Get holders holdings
                                            ngh_holdings = self.hol_w_hol[ngh_holder]
                                            # Filter holdings
                                            ngh_holdings_ids = self.ids_for_change(ngh_holdings, local_changables)
                                            if ngh_holdings_ids:
                                                # Filter out nghs
                                                filtered_ngh_holdings_ids = [h_id for h_id in ngh_holdings_ids if h_id not in ngh_ids]

                                                ngh_feat_id = nghfeat.attribute(self.id_attribute)
                                                if ngh_feat_id in local_changables:
                                                    filtered_ngh_holdings_ids.append(ngh_feat_id)

                                                    holder_combinations = []
                                                    if len(filtered_holdings_ids) + 1 < 2:
                                                        for L in range(len(filtered_holdings_ids) + 1):
                                                            for subset in itertools.combinations(filtered_holdings_ids, L):
                                                                if len(subset) >= 1 and subset not in holder_combinations and len(subset) <= 10:
                                                                    holder_combinations.append(subset)
                                                    else:
                                                        for L in range(2):
                                                            for subset in itertools.combinations(filtered_holdings_ids, L):
                                                                if len(subset) >= 1 and subset not in holder_combinations and len(subset) <= 10:
                                                                    holder_combinations.append(subset)

                                                    ngh_combinations = []
                                                    if len(filtered_ngh_holdings_ids) + 1 < 2:
                                                        for L in range(len(filtered_ngh_holdings_ids) + 1):
                                                            for subset in itertools.combinations(filtered_ngh_holdings_ids, L):
                                                                if len(subset) >= 1 and  ngh_feat_id in subset and subset not in ngh_combinations and len(subset) <= 10:
                                                                    ngh_combinations.append(subset)
                                                    else:
                                                        for L in range(2):
                                                            for subset in itertools.combinations(filtered_ngh_holdings_ids, L):
                                                                if len(subset) >= 1 and  ngh_feat_id in subset and subset not in ngh_combinations and len(subset) <= 10:
                                                                    ngh_combinations.append(subset)

                                                    holder_comb_totals = []
                                                    for comb in holder_combinations:
                                                        temp_area = self.calculate_combo_area(comb)
                                                        holder_comb_totals.append(temp_area)

                                                    ngh_holder_comb_totals = []
                                                    for comb in ngh_combinations:
                                                        temp_area = self.calculate_combo_area(comb)
                                                        ngh_holder_comb_totals.append(temp_area)

                                                    total_areas_difference = []
                                                    holder_new_total_areas = []
                                                    ngh_new_total_areas = []
                                                    possible_holder_changes = []
                                                    possible_ngh_changes = []
                                                    for holder_comb in holder_comb_totals:
                                                        for ngh_comb in ngh_holder_comb_totals:
                                                            new_holder_total_area = holder_total_area - holder_comb + ngh_comb
                                                            if self.check_total_area_threshold(new_holder_total_area, holder):
                                                                new_ngh_total_area = ngh_holder_total_area - ngh_comb + holder_comb
                                                                if self.check_total_area_threshold(new_ngh_total_area, ngh_holder):
                                                                    total_areas_difference.append(new_holder_total_area-holder_total_area)
                                                                    holder_new_total_areas.append(new_holder_total_area)
                                                                    ngh_new_total_areas.append(new_ngh_total_area)
                                                                    possible_holder_changes.append(holder_combinations[holder_comb_totals.index(holder_comb)])
                                                                    possible_ngh_changes.append(ngh_combinations[ngh_holder_comb_totals.index(ngh_comb)])
                                                    if possible_holder_changes:
                                                        indexer = total_areas_difference.index(min(total_areas_difference))
                                                        smallest = possible_holder_changes[indexer]
                                                        ngh_cmbs = possible_ngh_changes[indexer]
                                                        if len(smallest) > 1 and len(ngh_cmbs) > 1:
                                                            #many to many change
                                                            commitMessage = f'Change {str(self.counter)} for {ngh_feat_id} (holder:{ngh_holder}) as neighbour of {seed} (holder:{holder}): {smallest} for {ngh_cmbs}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                                            for hold in smallest:
                                                                self.set_new_attribute(layer, hold, ','.join(ngh_cmbs), self.nid_attribute)
                                                                self.set_new_attribute(layer, hold, ngh_holder, self.nholder_attribute)
                                                                local_changables.pop(local_changables.index(hold))
                                                                changes_ids.append(hold)
                                                                self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(hold))
                                                                self.hol_w_hol[ngh_holder].append(hold)
                                                            for ngh in ngh_cmbs:
                                                                self.set_new_attribute(layer, ngh, ','.join(smallest), self.nid_attribute)
                                                                self.set_new_attribute(layer, ngh, holder, self.nholder_attribute)
                                                                local_changables.pop(local_changables.index(ngh))
                                                                changes_ids.append(ngh)
                                                                self.hol_w_hol[ngh_holder].pop(self.hol_w_hol[ngh_holder].index(ngh))
                                                                self.hol_w_hol[holder].append(ngh)
                                                            self.global_changables.pop(self.global_changables.index(ngh_feat_id))
                                                            self.seeds[holder].append(ngh_feat_id)
                                                            local_total_areas[holder] = holder_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            local_total_areas[ngh_holder] = ngh_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            self.counter += 1
                                                        elif len(smallest) > 1 and len(ngh_cmbs) == 1 or len(smallest) == 1 and len(ngh_cmbs) > 1:
                                                            #many to one change
                                                            commitMessage = f'Change {str(self.counter)} for {ngh_feat_id} (holder:{ngh_holder}) as neighbour of {seed} (holder:{holder}): {smallest} for {ngh_cmbs}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                                            if len(smallest) > 1:
                                                                for hold in smallest:
                                                                    self.set_new_attribute(layer, hold, ngh_cmbs[0], self.nid_attribute)
                                                                    self.set_new_attribute(layer, hold, ngh_holder, self.nholder_attribute)
                                                                    local_changables.pop(local_changables.index(hold))
                                                                    changes_ids.append(hold)
                                                                    self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(hold))
                                                                    self.hol_w_hol[ngh_holder].append(hold)
                                                                self.set_new_attribute(layer, ngh_feat_id, ','.join(smallest), self.nid_attribute)
                                                                self.set_new_attribute(layer, ngh_feat_id, holder, self.nholder_attribute)
                                                                local_changables.pop(local_changables.index(ngh_feat_id))
                                                                changes_ids.append(ngh_feat_id)
                                                                self.hol_w_hol[ngh_holder].pop(self.hol_w_hol[ngh_holder].index(ngh_feat_id))
                                                                self.hol_w_hol[holder].append(ngh_feat_id)
                                                            else:
                                                                for ngh in ngh_cmbs:
                                                                    self.set_new_attribute(layer, ngh, smallest[0], self.nid_attribute)
                                                                    self.set_new_attribute(layer, ngh, holder, self.nholder_attribute)
                                                                    local_changables.pop(local_changables.index(ngh))
                                                                    changes_ids.append(ngh)
                                                                    self.hol_w_hol[ngh_holder].pop(self.hol_w_hol[ngh_holder].index(ngh))
                                                                    self.hol_w_hol[holder].append(ngh)
                                                                self.set_new_attribute(layer, smallest[0], ','.join(ngh_cmbs), self.nid_attribute)
                                                                self.set_new_attribute(layer, smallest[0], ngh_holder, self.nholder_attribute)
                                                                local_changables.pop(local_changables.index(smallest[0]))
                                                                changes_ids.append(smallest[0])
                                                                self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(smallest[0]))
                                                                self.hol_w_hol[ngh_holder].append(smallest[0])
                                                            self.global_changables.pop(self.global_changables.index(ngh_feat_id))
                                                            self.seeds[holder].append(ngh_feat_id)
                                                            local_total_areas[holder] = holder_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            local_total_areas[ngh_holder] = ngh_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            self.counter += 1
                                                        else:
                                                            #one to one change
                                                            commitMessage = f'Change {str(self.counter)} for {ngh_feat_id} (holder:{ngh_holder}) as neighbour of {seed} (holder:{holder}): {smallest} for {ngh_cmbs}'
                                                            logging.debug(commitMessage)
                                                            feedback.pushInfo(commitMessage)
                                                            self.set_new_attribute(layer, smallest[0], ngh_feat_id, self.nid_attribute)
                                                            self.set_new_attribute(layer, smallest[0], ngh_holder, self.nholder_attribute)
                                                            self.set_new_attribute(layer, ngh_feat_id, smallest[0], self.nid_attribute)
                                                            self.set_new_attribute(layer, ngh_feat_id, holder, self.nholder_attribute)
                                                            local_changables.pop(local_changables.index(ngh_feat_id))
                                                            local_changables.pop(local_changables.index(smallest[0]))
                                                            changes_ids.append(smallest[0])
                                                            changes_ids.append(ngh_feat_id)
                                                            self.hol_w_hol[ngh_holder].pop(self.hol_w_hol[ngh_holder].index(ngh_feat_id))
                                                            self.hol_w_hol[holder].append(ngh_feat_id)
                                                            self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(smallest[0]))
                                                            self.hol_w_hol[ngh_holder].append(smallest[0])
                                                            self.global_changables.pop(self.global_changables.index(ngh_feat_id))
                                                            self.seeds[holder].append(ngh_feat_id)
                                                            local_total_areas[holder] = holder_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            local_total_areas[ngh_holder] = ngh_new_total_areas[total_areas_difference.index(min(total_areas_difference))]
                                                            self.counter += 1
                                changables.extend(changes_ids)
                    elif len(seeds) == 0:
                        continue

            if turn == 1:
                changes = copy.deepcopy(self.counter)
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
            elif self.algorithm_index == 3 and changes == 1 and turn <= (self.steps/2)-2:
                changes = copy.deepcopy(self.counter)
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter-changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter-changes}')
                if changes == self.counter:
                    changer = False
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.nid_attribute))
                    indexes.append(layer.fields().indexFromName(self.nholder_attribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                elif (self.algorithm_index == 0 or self.algorithm_index == 3) and (turn == self.steps-3):
                    changer = False
                elif self.algorithm_index == 2 and turn == (self.steps/2)-3:
                    changer = False
                else:
                    changes = copy.deepcopy(self.counter)
            feedback.setCurrentStep(1 + turn)
            feedback.pushInfo(f'Save turn results to the file')
            if feedback.isCanceled():
                return {}
        return layer

    def closer(self, layer, feedback, seeds=None):
        self.counter = 0
        changes = 1
        changer = True

        if seeds:
            self.seeds = seeds
            turn = int(self.nholder_attribute.split('_')[0])
            if self.algorithm_index == 2:
                if turn == (self.steps/2)-3:
                    self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])) + self.nholder_attribute[2:]
                    self.nid_attribute = str(int(self.nid_attribute.split('_')[0])) + self.nid_attribute[2:]
                else:
                    if turn >= 10:
                        self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])-1) + self.nholder_attribute[2:]
                        self.nid_attribute = str(int(self.nid_attribute.split('_')[0])-1) + self.nid_attribute[2:]
                    else:
                        self.nholder_attribute = str(int(self.nholder_attribute.split('_')[0])-1) + self.nholder_attribute[1:]
                        self.nid_attribute = str(int(self.nid_attribute.split('_')[0])-1) + self.nid_attribute[1:]
        else:
            self.global_changables = self.get_changable_holdings()
            turn = 0

        local_total_areas = copy.deepcopy(self.holder_total_area)
        feedback.pushInfo('Closer algorithm started') 
        while changer:
            turn += 1
            layer = self.set_turn_attributes(layer, turn)
            hol_w_hol = copy.deepcopy(self.hol_w_hol)
            changables = copy.deepcopy(self.global_changables)
            feedback.pushInfo(f'Turn {turn}')  
            for holder, holdings in hol_w_hol.items():
                if holder != 'NULL':
                    holder_total_area = local_total_areas[holder]
                    seed_list = self.seeds[holder]
                    if len(seed_list) == 1:
                        seed = seed_list[0]
                    else:
                        continue
                    in_distance = self.filtered_distances[seed]
                    distance_changes = self.get_changable_holdings(in_distance)
                    filtered_holdings_ids = self.ids_for_change(holdings, distance_changes)
                    filtered_holdings_ids = self.ids_for_change(filtered_holdings_ids, changables)
                    filtered_holdings_ids = self.ids_for_change(filtered_holdings_ids, self.hol_w_hol[holder])
                    sorted_distances = [(y, x) for y, x in zip(list(in_distance.values()), list(in_distance.keys())) if x in filtered_holdings_ids]
                    sorted_distances.sort()
                    filtered_holdings_ids = [key for value, key in sorted_distances[:5]]

                    holder_combinations = []
                    for L in range(len(filtered_holdings_ids) + 1):
                        for subset in itertools.combinations(filtered_holdings_ids, L):
                            if len(subset) >= 1 and subset not in holder_combinations and len(subset) <= 10:
                                holder_combinations.append(subset)

                    temp_holder_combo = None
                    temp_ch_combo = None
                    temp_holder_total_area = None
                    temp_ch_total_area = None
                    change_holder = None
                    measure = None

                    filtered_local_changables = []
                    for dist in distance_changes:
                        if dist not in filtered_holdings_ids and dist in changables:
                            filtered_local_changables.append(dist)

                    ch_holders = []
                    for local_ch in filtered_local_changables:
                        for all_holder, all_holdings in self.hol_w_hol.items():
                            if local_ch in all_holdings and all_holder not in ch_holders and all_holder != 'NULL':
                                #if len(all_holder) == 1:
                                ch_holders.append(all_holder)

                    for ch_holder in ch_holders:
                        change_holder_seed = self.seeds[ch_holder][0]
                        filtered_local_ch_holdings = [hold for hold in self.hol_w_hol[ch_holder] if
                                                        hold in filtered_local_changables and hold != change_holder_seed]

                        ch_combinations = []
                        for L in range(len(filtered_local_ch_holdings) + 1):
                            for subset in itertools.combinations(filtered_local_ch_holdings, L):
                                if len(subset) >= 1 and subset not in ch_combinations and len(subset) <= 10:
                                    ch_max_distance = self.max_distance(subset, change_holder_seed)
                                    its_closer = False
                                    for holder_comb in holder_combinations:
                                        holder_max_distance = self.max_distance(holder_comb, seed)
                                        ch_closer = self.is_closer(holder_max_distance, subset, seed)
                                        holder_closer = self.is_closer(ch_max_distance, holder_comb, change_holder_seed)
                                        if ch_closer and holder_closer:
                                            its_closer = True
                                            break
                                    if its_closer:
                                        ch_combinations.append(subset)

                        if len(holder_combinations) > 0 and len(ch_combinations) > 0:
                            for holder_comb in holder_combinations:
                                for ch_comb in ch_combinations:
                                    new_holder_total_area = holder_total_area - self.calculate_combo_area(holder_comb) + self.calculate_combo_area(ch_comb)
                                    if self.check_total_area_threshold(new_holder_total_area, holder):
                                        new_ch_total_area = local_total_areas[ch_holder] - self.calculate_combo_area(ch_comb) + self.calculate_combo_area(holder_comb)
                                        if self.check_total_area_threshold(new_ch_total_area, ch_holder):
                                            local_measure = sum([self.calculate_composite_number(seed, temp_id) for temp_id in holder_comb])
                                            if not measure:
                                                change_holder = ch_holder
                                                temp_holder_combo = holder_comb
                                                temp_ch_combo = ch_comb
                                                measure = local_measure
                                                temp_holder_total_area = new_holder_total_area
                                                temp_ch_total_area = new_ch_total_area
                                            else:
                                                if measure < local_measure:
                                                    change_holder = ch_holder
                                                    temp_holder_combo = holder_comb
                                                    temp_ch_combo = ch_comb
                                                    measure = local_measure
                                                    temp_holder_total_area = new_holder_total_area
                                                    temp_ch_total_area = new_ch_total_area
                    if measure:
                        if len(temp_holder_combo) > 1 and len(temp_ch_combo) > 1:
                            #many to many change
                            for hold in temp_holder_combo:
                                self.set_new_attribute(layer, hold, ','.join(temp_holder_combo), self.nid_attribute)
                                self.set_new_attribute(layer, hold, change_holder, self.nholder_attribute)
                                self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(hold))
                                self.hol_w_hol[change_holder].append(hold)
                                changables.pop(changables.index(hold))
                            for ch in temp_ch_combo:
                                self.set_new_attribute(layer, ch, ','.join(temp_ch_combo), self.nid_attribute)
                                self.set_new_attribute(layer, ch, holder, self.nholder_attribute)
                                self.hol_w_hol[change_holder].pop(self.hol_w_hol[change_holder].index(ch))
                                self.hol_w_hol[holder].append(ch)
                                changables.pop(changables.index(ch))
                            local_total_areas[holder] = temp_holder_total_area
                            local_total_areas[change_holder] = temp_ch_total_area
                            self.counter += 1
                            commitMessage = f'Change {str(self.counter)} for {temp_ch_combo} (holder:{change_holder}) to get closer to {seed} (holder:{holder}): {temp_holder_combo} for {temp_ch_combo}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)
                        elif len(temp_holder_combo) > 1 and len(temp_ch_combo) == 1 or len(temp_holder_combo) == 1 and len(temp_ch_combo) > 1:
                            #many to one change
                            if len(temp_holder_combo) > 1:
                                for hold in temp_holder_combo:
                                    self.set_new_attribute(layer, hold, temp_ch_combo[0], self.nid_attribute)
                                    self.set_new_attribute(layer, hold, change_holder, self.nholder_attribute)
                                    self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(hold))
                                    self.hol_w_hol[change_holder].append(hold)
                                    changables.pop(changables.index(hold))
                                self.set_new_attribute(layer, temp_ch_combo[0], holder, self.nholder_attribute)
                                self.set_new_attribute(layer, temp_ch_combo[0], ','.join(temp_holder_combo), self.nid_attribute)
                                self.hol_w_hol[change_holder].pop(self.hol_w_hol[change_holder].index(temp_ch_combo[0]))
                                self.hol_w_hol[holder].append(temp_ch_combo[0])
                                changables.pop(changables.index(temp_ch_combo[0]))
                                self.counter += 1
                                commitMessage = f'Change {str(self.counter)} for {temp_ch_combo} (holder:{change_holder}) to get closer to {seed} (holder:{holder}): {temp_holder_combo} for {temp_ch_combo[0]}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            else:
                                for ch in temp_ch_combo:
                                    self.set_new_attribute(layer, ch, temp_holder_combo[0], self.nid_attribute)
                                    self.set_new_attribute(layer, ch, holder, self.nholder_attribute)
                                    self.hol_w_hol[change_holder].pop(self.hol_w_hol[change_holder].index(ch))
                                    self.hol_w_hol[holder].append(ch)
                                    changables.pop(changables.index(ch))
                                self.set_new_attribute(layer, temp_holder_combo[0], change_holder, self.nholder_attribute)
                                self.set_new_attribute(layer, temp_holder_combo[0], ','.join(temp_ch_combo), self.nid_attribute)
                                self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(temp_holder_combo[0]))
                                self.hol_w_hol[change_holder].append(temp_holder_combo[0])
                                changables.pop(changables.index(temp_holder_combo[0]))
                                self.counter += 1
                                commitMessage = f'Change {str(self.counter)} for {temp_ch_combo} (holder:{change_holder}) to get closer to {seed} (holder:{holder}): {temp_holder_combo[0]} for {temp_ch_combo}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            local_total_areas[holder] = temp_holder_total_area
                            local_total_areas[change_holder] = temp_ch_total_area
                        else:
                            #one to one change
                            self.set_new_attribute(layer, temp_holder_combo[0], temp_ch_combo[0], self.nid_attribute)
                            self.set_new_attribute(layer, temp_holder_combo[0], change_holder, self.nholder_attribute)
                            self.set_new_attribute(layer, temp_ch_combo[0], temp_holder_combo[0], self.nid_attribute)
                            self.set_new_attribute(layer, temp_ch_combo[0], holder, self.nholder_attribute)
                            self.hol_w_hol[change_holder].pop(self.hol_w_hol[change_holder].index(temp_ch_combo[0]))
                            self.hol_w_hol[holder].append(temp_ch_combo[0])
                            self.hol_w_hol[holder].pop(self.hol_w_hol[holder].index(temp_holder_combo[0]))
                            self.hol_w_hol[change_holder].append(temp_holder_combo[0])
                            changables.pop(changables.index(temp_ch_combo[0]))
                            changables.pop(changables.index(temp_holder_combo[0]))
                            local_total_areas[holder] = temp_holder_total_area
                            local_total_areas[change_holder] = temp_ch_total_area
                            self.counter += 1
                            commitMessage = f'Change {str(self.counter)} for {temp_ch_combo[0]} (holder:{change_holder}) as neighbour of {seed} (holder:{holder}): {temp_holder_combo[0]} for {temp_ch_combo[0]}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)                 
                if feedback.isCanceled():
                    self.end_logging() 
                    return {}

            if feedback.isCanceled():
                self.end_logging() 
                return {}
            if turn == 1:
                changes = copy.deepcopy(self.counter)
                self.filter_touching_features(layer) 
                logging.debug(f'Changes in turn {turn}: {self.counter}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            else:
                logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
                feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
                if changes == self.counter or abs(self.counter - changes) < int(layer.featureCount()*0.01):
                    changer = False
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.nid_attribute))
                    indexes.append(layer.fields().indexFromName(self.nholder_attribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                elif self.algorithm_index == 1 or self.algorithm_index == 2:
                    if turn == self.steps-3:
                        self.filter_touching_features(layer)
                        changer = False
                elif self.algorithm_index == 3:
                    if turn == (self.steps/2)-3:
                        self.filter_touching_features(layer)
                        changer = False
                else:
                    changes = copy.deepcopy(self.counter)
                    self.filter_touching_features(layer)
            feedback.setCurrentStep(1+turn)
            feedback.pushInfo(f'Save turn results to the file')

        return layer

    def create_new_attribute(self, layer, turn, adj, typer=QVariant.String, lenght=50):
        field_name = f'{turn}_{adj}'
        layer_attributes = self.get_attributes_names(layer)
        if field_name in layer_attributes:
            counter = 0
            while field_name in layer_attributes:
                field_name = f"{field_name}{counter}"
                counter += 1
        layer.startEditing()
        data_provider = layer.dataProvider()
        if typer == QVariant.Int:
            data_provider.addAttributes([QgsField(field_name, typer)])
        else:
            data_provider.addAttributes([QgsField(field_name, typer, len=lenght)])
        layer.updateFields()
        return layer, field_name

    def set_new_attribute(self, layer, feature_id, new_value, field):
        expression = ''
        expression += f'"{self.id_attribute}" = \'{feature_id}\''
        layer.selectByExpression(expression)
        index = self.get_attributes_names(layer).index(field)
        layer.startEditing()
        for feature in layer.selectedFeatures():
            layer.changeAttributeValue(feature.id(), index, new_value)
        layer.commitChanges()

    def set_turn_attributes(self, layer, turn):
        layer, new_id = self.create_new_attribute(layer, turn, 'id', lenght=32)
        layer, new_holder = self.create_new_attribute(layer, turn, 'holder', typer=self.holder_attr_type, lenght=self.holder_attr_len)
        if turn == 1:
            layer.startEditing()
            for feature in layer.getFeatures():
                layer.changeAttributeValue(feature.id(), self.get_attributes_names(layer).index(new_holder),
                                           str(feature.attribute(self.holder_attribute)))
            layer.commitChanges()
        else:
            layer.startEditing()
            for feature in layer.getFeatures():
                layer.changeAttributeValue(feature.id(), self.get_attributes_names(layer).index(new_holder),
                                           str(feature.attribute(self.nholder_attribute)))
            layer.commitChanges()
        self.nid_attribute = new_id
        self.nholder_attribute = new_holder
        return layer

    """
    def create_distance_matrix(self, layer):
        alg_params = {
        'INPUT':layer,
        'ALL_PARTS': False,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        centroids = processing.run("native:centroids", alg_params)['OUTPUT']
        alg_params = {
        'INPUT':centroids,
        'INPUT_FIELD': self.id_attribute,
        'TARGET': centroids,
        'TARGET_FIELD': self.id_attribute,
        'MATRIX_TYPE': 1,
        'NEAREST_POINTS': 0,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        matrix = processing.run("qgis:distancematrix", alg_params)['OUTPUT']
        distance_matrix = {}
        names = self.get_attributes_names(matrix)
        features = matrix.getFeatures()
        calculator = 0
        for feature in features:
            temp_dict = {}
            for field in names:
                value = feature.attribute(field)
                temp_dict[field] = value
            distance_matrix[feature.attribute('ID')] = temp_dict
            calculator += 1
            logging.debug(calculator)

        return distance_matrix, names
    """
    def create_distance_matrix(self, layer):
        max_features = 0
        feats = [feat for feat in layer.getFeatures()]
        for seed in self.seeds.values():
            if type(seed[0]) == str:
                expression = f'"{self.id_attribute}" = \'{seed[0]}\''
                layer.selectByExpression(expression)
                sel_feat = layer.selectedFeatures()[0]
                geom_buffer = sel_feat.geometry().buffer(self.distance+500, -1)
                local_max_features = 0
                for feat in feats:
                    if feat.geometry().intersects(geom_buffer):
                        local_max_features += 1
                if local_max_features > max_features:
                    max_features = local_max_features
                layer.removeSelection()

        alg_params = {
        'INPUT':layer,
        'ALL_PARTS': False,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        centroids = processing.run("native:centroids", alg_params)['OUTPUT']
        alg_params = {
        'INPUT':centroids,
        'INPUT_FIELD': self.id_attribute,
        'TARGET': centroids,
        'TARGET_FIELD': self.id_attribute,
        'MATRIX_TYPE': 0,
        'NEAREST_POINTS': max_features,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        matrix = processing.run("qgis:distancematrix", alg_params)['OUTPUT']
        distance_matrix = {}
        names = self.get_attributes_names(matrix)
        features = matrix.getFeatures()
        for feature in features:
            input_id = feature.attribute('InputID')
            target_id = feature.attribute('TargetID')
            value = feature.attribute('Distance')
            if input_id in distance_matrix.keys():
                distance_matrix[input_id][target_id] = value
            else:
                distance_matrix[input_id] = {}
                distance_matrix[input_id][target_id] = value
            #calculator += 1
        """
        calculator = 0
        for feature in features:
            temp_dict = {}
            for field in names:
                value = feature.attribute(field)
                temp_dict[field] = value
            distance_matrix[feature.attribute('ID')] = temp_dict
            calculator += 1
            logging.debug(calculator)
        """

        return distance_matrix, names

    def filter_distance_matrix(self):
        filtered_matrix = {}
        for key, value in self.distance_matrix.items():
            sorted_distances = [(y, x) for y, x in zip(list(value.values()), list(value.keys())) if x != 'ID']
            sorted_distances.sort()
            sub_filtered_matrix = {}
            for value_2, key_2 in sorted_distances:
                if value_2 <= self.distance:
                    sub_filtered_matrix[key_2] = value_2
                else:
                    break
            filtered_matrix[key] = sub_filtered_matrix
        return filtered_matrix

    def create_merge_feature(self, layer, features_id):
        feats = [feat for feat in layer.getFeatures()]
        for turn, feat in enumerate(features_id):
            if turn == 0:
                expression = f'"{self.id_attribute}" = \'{feat}\''
            else:
                expression += f'OR "{self.id_attribute}" = \'{feat}\''
        layer.selectByExpression(expression)
        sel_feat = layer.selectedFeatures()
        geom = None
        for feat in sel_feat:
            if geom == None:
                geom = feat.geometry()
            else:
                geom = geom.combine(feat.geometry())
        epsg = layer.crs().geographicCrsAuthId()[-4:]
        lyr = QgsVectorLayer(f"Polygon?crs=epsg:{epsg}", "merged", "memory")
        lyr.dataProvider().addFeatures([geom])
        return lyr

    def check_total_area_threshold(self, total_area, holder):
        minimal_bound = self.holder_total_area[holder] - (self.holder_total_area[holder] * (self.tolerance / 100))
        maximal_bound = self.holder_total_area[holder] + (self.holder_total_area[holder] * (self.tolerance / 100))
        if total_area >= minimal_bound and total_area <= maximal_bound:
            return True
        else:
            return False

    def calculate_combo_area(self, combo):
        temp_area = 0
        for comb in combo:
            temp_area += self.hol_w_aea[comb]
        return temp_area

    def is_closer(self, threshold_distance, ids, seed):
        is_closer_bool = True
        for id in ids:
            distance = self.distance_matrix[seed][id]
            if distance > threshold_distance:
                is_closer_bool = False
        return is_closer_bool

    def max_distance(self, ids, seed):
        max_distance = 0
        for id in ids:
            distance = self.distance_matrix[seed][id]
            if distance > max_distance:
                max_distance = distance
        return max_distance

    def calculate_composite_number(self, seed, id):
        area = self.hol_w_aea[id]
        distance = self.distance_matrix[seed][id]
        return area*distance

    def filter_touching_features(self, layer):
        alg_params =  {
        'INPUT':layer,
        'FIELD':[self.nholder_attribute],
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        dissolved_layer = processing.run("native:dissolve", alg_params)['OUTPUT']

        alg_params = {
        'INPUT':dissolved_layer,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        simplied_layer = processing.run("native:multiparttosingleparts", alg_params)['OUTPUT']

        for turn, seed in enumerate(list(self.seeds.values())):
            if turn == 0:
                expression = f'"{self.id_attribute}" = \'{seed}\''
            else:
                expression += f'OR "{self.id_attribute}" = \'{seed}\''
        layer.selectByExpression(expression)

        alg_params = {
            'INPUT': simplied_layer,
            'PREDICATE':[1],
            'METHOD' : 0,
            'INTERSECT':QgsProcessingFeatureSourceDefinition(layer.source(), selectedFeaturesOnly=True, featureLimit=-1, geometryCheck=QgsFeatureRequest.GeometryAbortOnInvalid),
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        seed_mergs = processing.run("native:extractbylocation", alg_params)['OUTPUT']

        alg_params = {
            'INPUT': layer,
            'PREDICATE':[6],
            'METHOD' : 0,
            'INTERSECT':seed_mergs,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        processing.run("native:selectbylocation", alg_params)['OUTPUT']
        selfeatures = layer.selectedFeatures()
        for feature in selfeatures:
            id_value = feature.attribute(self.id_attribute)
            self.global_changables.pop(self.global_changables.index(id_value))

    def create_merged_file(self, layer, directory, timestamp):
        last_nholder_attribute = int(self.nholder_attribute.split('_')[0])
        if last_nholder_attribute == self.steps-2:
            if self.steps-2 >= 10:
                attribute_name = str(last_nholder_attribute) + self.nholder_attribute[2:]
            else:
                attribute_name = str(last_nholder_attribute) + self.nholder_attribute[1:]
        else:
            if last_nholder_attribute >= 10:
                attribute_name = str(last_nholder_attribute-1) + self.nholder_attribute[2:]
            else:
                attribute_name = str(last_nholder_attribute-1) + self.nholder_attribute[1:]
        alg_params =  {
        'INPUT':layer,
        'FIELD':[attribute_name],
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        dissolved_layer = processing.run("native:dissolve",alg_params)['OUTPUT']
        alg_params = {
        'INPUT':dissolved_layer,
        'OUTPUT':'TEMPORARY_OUTPUT'
        }
        simplied_layer = processing.run("native:multiparttosingleparts", alg_params)['OUTPUT']
        simplied_layer.setName(f'{os.path.basename(layer.source())[:-4]}')
        simplied_layer.commitChanges()
        final_layer = self.create_temp_layer(simplied_layer, directory, "merged")
        return final_layer