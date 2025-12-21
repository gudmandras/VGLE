import copy
import itertools
import logging
import random
import os
import time

from . import vgle_utils, vgle_layers, vgle_features, vgle_multi
from qgis.core import QgsApplication
from PyQt5.QtCore import QTimer, QEventLoop, pyqtSlot, QCoreApplication
from qgis.core import QgsFeatureRequest


def neighbours(self, layer, feedback, totalAreas=None, context=None):
    """
    DESCRIPTION: Function for make swap to get group holder's holdings around their seed polygons.
    The function try to swap the neighbour polygons for other holdings of the holder.
    INPUTS:
            layer: QgsVectorLayer
            feedback: QgsProcessingMultiStepFeedback
    OUTPUTS: QgsVectorLayer
    """
    #import ptvsd
    #ptvsd.debug_this_thread()
    changes = 0
    changer = True
    self.globalChangables = vgle_utils.getChangableHoldings(self)

    try:
        turn = int(self.actualHolderAttribute.split('_')[0])
        if self.algorithmIndex == 3:
            if turn == (self.steps/2)-3:
                self.actualHolderAttribute = \
                    str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
            else:
                if turn >= 10:
                    self.actualHolderAttribute = \
                        str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = \
                        str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                else:
                    self.actualHolderAttribute = \
                        str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                    self.actualIdAttribute = \
                        str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                turn -= 1
    except AttributeError:
        turn = 0

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Neighbours algorithm start')

    while changer:
        turn += 1
        self.actualIdAttribute, self.actualHolderAttribute, layer = vgle_layers.setTurnAttributes(self, layer, turn)
        not_changables = []
        feedback.pushInfo(f'Turn {turn}')
        for holder, holdings in self.holdersWithHoldings.items():
            if holder == 'NULL':
                continue

            seeds = self.seeds[holder]
            
            if not seeds:
                continue

            for seed in seeds:
                neighboursIds, neighboursLayer = vgle_features.getNeighbours(self.idAttribute, layer, seed, context=context, feedback=feedback)
                inDistance = self.filteredDistanceMatrix[seed]
                distanceChanges = vgle_utils.getChangableHoldings(self, inDistance)
                localChangables = [distance for distance in distanceChanges
                                    if distance in self.globalChangables and distance not in not_changables]
                if not localChangables:
                    continue

                holdingsIds = []
                changesIds = []
                
                for holdingId in holdings:
                    if holdingId in neighboursIds:
                        if holdingId not in self.seeds[holder]:
                            self.seeds[holder].append(holdingId)
                    else:
                        holdingsIds.append(holdingId)

                neighboursFeatures = neighboursLayer.getFeatures()
                for nghfeat in neighboursFeatures:
                    # Get holder total area
                    holderTotalArea = holdersLocalTotalArea[holder]
                    # Filter holdings
                    filteredHolderHoldingsIds = vgle_utils.idsForChange(holdingsIds, localChangables)
                    if not filteredHolderHoldingsIds:
                        continue
                    
                    # Get ngh holder name
                    neighbourHolder = nghfeat.attribute(self.actualHolderAttribute)
                    if neighbourHolder != 'NULL' and neighbourHolder != holder:
                        try:
                            targetHolderSeed = self.seeds[neighbourHolder][0]
                        except IndexError:
                            if self.useSingle:
                                targetHolderSeed = False
                            else:
                                continue
                        except KeyError:
                            try:
                                neighbourHolder = int(neighbourHolder)
                                targetHolderSeed = self.seeds[int(neighbourHolder)][0]
                            except KeyError:
                                if self.useSingle:
                                    targetHolderSeed = False
                                else:
                                    continue
                        # Get holder total area
                        neighbourHolderTotalArea = holdersLocalTotalArea[neighbourHolder]
                        # Get holders holdings
                        neighbourHoldings = self.holdersWithHoldings[neighbourHolder]
                        # Filter holdings
                        neighbourHoldingsIds = vgle_utils.idsForChange(neighbourHoldings,
                                                                        localChangables)
                        if not neighbourHoldingsIds:
                            continue
                        
                        # Filter out nghs
                        filteredNeighbourHoldingsIds = [holdingId for holdingId
                                                        in neighbourHoldingsIds
                                                        if holdingId not in neighboursIds]

                        neighbourTargetFeatureId = nghfeat.attribute(self.idAttribute)
                        if not neighbourTargetFeatureId in localChangables:
                            continue
                        if not targetHolderSeed:
                            neighbourHoldingsCombinations = [[neighbourTargetFeatureId]]
                        else: 
                            neighbourHoldingsCombinations = \
                                vgle_utils.combine_with_constant_in_all(
                                    filteredNeighbourHoldingsIds, neighbourTargetFeatureId)

                        holdingCombinations = vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds)

                        holderCombinationForChange = None
                        neighbourCombinationForChange = None
                        holderNewTotalArea = 0
                        neighbourNewTotalArea = 0
                        totalAreaDifference = -999
                        
                        lenTurn = 0
                        for combination in holdingCombinations:
                            combinationLenght = len(combination)
                            combTurn = 0
                            if self.simply:
                                if combTurn > 10000*combinationLenght and lenTurn > 20000:
                                    break
                            for neighbourCombination in neighbourHoldingsCombinations:                
                                lenTurn += 1  
                                combTurn += 1            
                                temporaryHolderArea = vgle_utils.calculateCombinationArea(self, combination)                             
                                if self.strict:
                                    # Distance conditions
                                    if self.useSingle and not targetHolderSeed:
                                        holderMaxDistance = vgle_features.maxDistance(self, combination, seed, layer)
                                        holderAvgDistanceOld = vgle_features.avgDistance(self, combination, seed, layer)
                                        holderAvgDistanceNew = vgle_features.avgDistance(self, neighbourCombination, seed, layer)
                                        targetCloser = True
                                        targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                        holderCloser = vgle_utils.isCloser(self, targetMaxDistance, combination, targetHolderSeed, neighbourHolder, layer)
                                    else:
                                        targetMaxDistance = vgle_features.maxDistance(self, neighbourCombination, targetHolderSeed, layer)
                                        targetAvgDistanceOld = vgle_features.avgDistance(self, neighbourCombination, targetHolderSeed, layer)
                                        holderMaxDistance = vgle_features.maxDistance(self, combination, seed, layer)
                                        holderAvgDistanceOld = vgle_features.avgDistance(self, combination, seed, layer)
                                        holderAvgDistanceNew = vgle_features.avgDistance(self, neighbourCombination, seed, layer)
                                        targetAvgDistanceNew = vgle_features.avgDistance(self, combination, targetHolderSeed, layer)
                                        targetCloser = vgle_utils.isCloser(self, holderMaxDistance, neighbourCombination, seed, holder, layer)
                                        holderCloser = vgle_utils.isCloser(self, targetMaxDistance, combination, targetHolderSeed, neighbourHolder, layer)
                                else:
                                    targetCloser, holderCloser = True, True
                                    targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                    holderAvgDistanceNew, holderAvgDistanceOld = 1,2
                                if targetCloser and holderCloser:
                                    if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                                        #Weight condition
                                        temporaryTargetArea = vgle_utils.calculateCombinationArea(self, neighbourCombination)
                                        newHolderTotalArea = holderTotalArea - temporaryHolderArea + temporaryTargetArea
                                        newNeighbourTotalArea = neighbourHolderTotalArea - temporaryTargetArea + temporaryHolderArea
                                        thresholdHolder = vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder)
                                        thresholdNeighbour = vgle_utils.checkTotalAreaThreshold(self, newNeighbourTotalArea, neighbourHolder)
                                        difference = abs(newHolderTotalArea-holderTotalArea)
                                        if totalAreaDifference == -999:
                                            if thresholdHolder and thresholdNeighbour:
                                                holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination) - 1
                                                targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                if self.strict:
                                                    if holderNewHoldignNum > self.holdersHoldingNumber[holder] or targetNewHoldingNum > self.holdersHoldingNumber[neighbourHolder]:
                                                        continue
                                                holderCombinationForChange = combination
                                                neighbourCombinationForChange = neighbourCombination
                                                holderNewTotalArea = newHolderTotalArea
                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                totalAreaDifference = difference
                                        else:
                                            if thresholdHolder and thresholdNeighbour and difference < totalAreaDifference:
                                                holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination) - 1
                                                targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                if self.strict:
                                                    if holderNewHoldignNum > self.holdersHoldingNumber[holder] or targetNewHoldingNum > self.holdersHoldingNumber[neighbourHolder]:
                                                        continue
                                                holderCombinationForChange = combination
                                                neighbourCombinationForChange = neighbourCombination
                                                holderNewTotalArea = newHolderTotalArea
                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                totalAreaDifference = difference 

                        if holderCombinationForChange and neighbourCombinationForChange:       
                            vgle_layers.setAttributeValues(self, layer, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                            if self.stats:
                                self.interactionTable[holder][neighbourHolder] += 1
                                self.interactionTable[neighbourHolder][holder] += 1    
                            vgle_utils.update_areas_and_distances(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, seed, targetHolderSeed, localChangables, layer)
                            commitMessage = f'Change {self.counter} for {neighbourCombinationForChange} (holder:{neighbourHolder}) to get neighbour of {seed} (holder:{holder}): ' \
                                            f'{holderCombinationForChange} for {neighbourCombinationForChange}'
                            logging.debug(commitMessage)
                            feedback.pushInfo(commitMessage)

                            holdersLocalTotalArea[holder] = holderNewTotalArea
                            holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea                            
                not_changables.extend(changesIds)


        if self.counter == 0:
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            changer = False
            if (self.algorithmIndex == 0 or self.algorithmIndex == 3): 
                return None, None
        else:
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter-changes}') 
            logging.debug(f'Changes in turn {turn}: {self.counter-changes}')
            if changes == self.counter:
                changer = False
                layer.startEditing()
                indexes = []
                indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                layer.deleteAttributes(indexes)
                layer.updateFields()
                layer.commitChanges()
                return layer, holdersLocalTotalArea
            elif (self.algorithmIndex == 0 or self.algorithmIndex == 3) and (turn == self.steps-3):
                changer = False
            elif self.algorithmIndex == 2 and turn == (self.steps/2)-3:
                changer = False 
            else:
                changes = copy.deepcopy(self.counter)
        feedback.setCurrentStep(1 + turn)
        feedback.pushInfo(f'Save turn results to the file')
        if feedback.isCanceled():
            return None, None
    return layer, holdersLocalTotalArea


def neighbours_multi(self, layer, feedback, totalAreas=None):
    """
    DESCRIPTION: Function for make swap to get group holder's holdings around their seed polygons.
    The function try to swap the neighbour polygons for other holdings of the holder.
    INPUTS:
            layer: QgsVectorLayer
            feedback: QgsProcessingMultiStepFeedback
    OUTPUTS: QgsVectorLayer
    """
    #import ptvsd
    #ptvsd.debug_this_thread()
    maxCombTurn = 2000
    TIMEOUT_SECONDS = 20
    MAX_PARALLEL = os.cpu_count() - 2

    changes = 0
    changer = True
    self.globalChangables = vgle_utils.getChangableHoldings(self)

    try:
        turn = int(self.actualHolderAttribute.split('_')[0])
        if self.algorithmIndex == 3:
            if turn == (self.steps/2)-3:
                self.actualHolderAttribute = \
                    str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
            else:
                if turn >= 10:
                    self.actualHolderAttribute = \
                        str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = \
                        str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                else:
                    self.actualHolderAttribute = \
                        str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                    self.actualIdAttribute = \
                        str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                turn -= 1
    except AttributeError:
        turn = 0

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Neighbours algorithm start')

    while changer:
        turn += 1
        self.actualIdAttribute, self.actualHolderAttribute, layer = vgle_layers.setTurnAttributes(self, layer, turn)
        not_changables = []
        feedback.pushInfo(f'Turn {turn}')
        for holder, holdings in self.holdersWithHoldings.items():
            if holder == 'NULL':
                continue

            seeds = self.seeds[holder]
            
            if not seeds:
                continue

            for seed in seeds:
                neighboursIds, neighboursLayer = vgle_features.getNeighbours(self.idAttribute, layer, seed)
                inDistance = self.filteredDistanceMatrix[seed]
                distanceChanges = vgle_utils.getChangableHoldings(self, inDistance)
                localChangables = [distance for distance in distanceChanges
                                    if distance in self.globalChangables and distance not in not_changables]
                if not localChangables:
                    continue

                holdingsIds = []
                changesIds = []
                
                for holdingId in holdings:
                    if holdingId in neighboursIds:
                        if holdingId not in self.seeds[holder]:
                            self.seeds[holder].append(holdingId)
                    else:
                        holdingsIds.append(holdingId)

                #neighboursFeatures = neighboursLayer.getFeatures()
                ids_str = ", ".join(f"'{value}'" for value in neighboursIds)
                filter_expression = f'"{self.idAttribute}" IN ({ids_str})'
                request = QgsFeatureRequest().setFilterExpression(filter_expression)
                neighboursFeatures = list(layer.getFeatures(request))
                for nghfeat in neighboursFeatures:
                    # Get holder total area
                    holderTotalArea = holdersLocalTotalArea[holder]
                    # Filter holdings
                    filteredHolderHoldingsIds = vgle_utils.idsForChange(holdingsIds, localChangables)
                    if not filteredHolderHoldingsIds:
                        continue
                    
                    # Get ngh holder name
                    neighbourHolder = nghfeat.attribute(self.actualHolderAttribute)
                    if neighbourHolder != 'NULL' and neighbourHolder != holder:
                        try:
                            targetHolderSeed = self.seeds[neighbourHolder][0]
                        except IndexError:
                            if self.useSingle:
                                targetHolderSeed = False
                            else:
                                continue
                        except KeyError:
                            try:
                                neighbourHolder = int(neighbourHolder)
                                targetHolderSeed = self.seeds[int(neighbourHolder)][0]
                            except KeyError:
                                if self.useSingle:
                                    targetHolderSeed = False
                                else:
                                    continue
                        # Get holder total area
                        neighbourHolderTotalArea = holdersLocalTotalArea[neighbourHolder]
                        # Get holders holdings
                        neighbourHoldings = self.holdersWithHoldings[neighbourHolder]
                        # Filter holdings
                        neighbourHoldingsIds = vgle_utils.idsForChange(neighbourHoldings,
                                                                        localChangables)
                        if not neighbourHoldingsIds:
                            continue
                        
                        # Filter out nghs
                        filteredNeighbourHoldingsIds = [holdingId for holdingId
                                                        in neighbourHoldingsIds
                                                        if holdingId not in neighboursIds and holdingId not in not_changables]

                        neighbourTargetFeatureId = nghfeat.attribute(self.idAttribute)
                        if not neighbourTargetFeatureId in localChangables:
                            continue
                        if not targetHolderSeed:
                            neighbourHoldingsCombinations = [[neighbourTargetFeatureId]]
                        else: 
                            neighbourHoldingsCombinations = \
                                vgle_utils.combine_with_constant_in_all(
                                    filteredNeighbourHoldingsIds, neighbourTargetFeatureId)

                        holdingCombinations = vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds)

                        app = QgsApplication.instance()
                        task_manager = app.taskManager()
                        app.setMaxThreads(MAX_PARALLEL)
                        finished_count = 0
                        tasks_to_complete = 0
                        cleanup_initiated = False 
                        activeTasks = []
                        taskResults = []

                        def on_task_finished(success, result):
                            nonlocal finished_count, tasks_to_complete, loop, activeTasks
                            finished_count += 1
                            if success and result is not None:
                                taskResults.append(result)
                            else:
                                if result:
                                    feedback.pushInfo(result)

                            if finished_count >= tasks_to_complete:
                                cleanup_and_quit()


                        def cleanup_and_quit():
                            nonlocal cleanup_initiated, activeTasks, loop

                            if cleanup_initiated:
                                return
                            
                            cleanup_initiated = True

                            for task in activeTasks:
                                try:
                                    task.cancel()
                                except RuntimeError:
                                    pass

                            QCoreApplication.processEvents()
                            if loop.isRunning():
                                loop.quit()
                            if batch_timer.isActive():
                                batch_timer.stop()

                        lenTurn = 0
                        for combination in holdingCombinations:
                            lenTurn += 1
                            if self.simply:
                                if lenTurn >= maxCombTurn:
                                    break
                            for i, neighbourCombination in enumerate(neighbourHoldingsCombinations):
                                if self.simply:
                                    if i >= maxCombTurn:
                                        break
                                task = vgle_multi.NeighbourFunctionComparisonTask(f'Eval_{i}', holder, neighbourHolder, combination, neighbourCombination, seed, targetHolderSeed, holderTotalArea, neighbourHolderTotalArea, self, self.strict, self.useSingle, on_finished=on_task_finished)
                                task_manager.addTask(task)
                                activeTasks.append(task)

                        loop = QEventLoop()
                        tasks_to_complete = len(activeTasks)
                        batch_timer = QTimer()
                        batch_timer.setInterval(TIMEOUT_SECONDS * 1000)
                        batch_timer.setSingleShot(True)
                        batch_timer.timeout.connect(cleanup_and_quit)
                        
                        if tasks_to_complete > 0:
                            QCoreApplication.processEvents()
                            batch_timer.start()
                            loop.exec_()

                            if batch_timer.isActive():
                                batch_timer.stop()
                        
                        diff = False
                        for taskResult in taskResults:
                            if diff and taskResult[4] < diff:
                                holderCombinationForChange, neighbourCombinationForChange, holderNewTotalArea, neighbourNewTotalArea, diff = taskResult
                            else:
                                if taskResult[4]:
                                    holderCombinationForChange, neighbourCombinationForChange, holderNewTotalArea, neighbourNewTotalArea, diff = taskResult
                                else:
                                    continue

                        try:
                            if holderCombinationForChange and neighbourCombinationForChange:
                                changesIds.extend(holderCombinationForChange)
                                changesIds.extend(neighbourCombinationForChange)
                                vgle_layers.setAttributeValues(self, layer, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                                if self.stats:
                                    self.interactionTable[holder][neighbourHolder] += 1
                                    self.interactionTable[neighbourHolder][holder] += 1    
                                vgle_utils.update_areas_and_distances(self, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange, seed, targetHolderSeed, localChangables, layer)
                                commitMessage = f'Change {self.counter} for {neighbourCombinationForChange} (holder:{neighbourHolder}) to get neighbour of {seed} (holder:{holder}): ' \
                                                f'{holderCombinationForChange} for {neighbourCombinationForChange}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)

                                holdersLocalTotalArea[holder] = holderNewTotalArea
                                holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                del holderCombinationForChange, neighbourCombinationForChange, holderNewTotalArea, neighbourNewTotalArea, diff
                        except UnboundLocalError:
                            continue                            
                    not_changables.extend(changesIds)

        if turn == 1:
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            if self.counter == 0:
                changer = False
            else:
                changes = copy.deepcopy(self.counter)
        elif self.algorithmIndex == 3 and changes == 1 and turn <= (self.steps/2)-2:
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
                indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                layer.deleteAttributes(indexes)
                layer.updateFields()
                layer.commitChanges()
            elif (self.algorithmIndex == 0 or self.algorithmIndex == 3) and (turn == self.steps-3):
                changer = False
            elif self.algorithmIndex == 2 and turn == (self.steps/2)-3:
                changer = False
            else:
                changes = copy.deepcopy(self.counter)
        feedback.setCurrentStep(1 + turn)
        feedback.pushInfo(f'Save turn results to the file')
        if feedback.isCanceled():
            return None, None
    return layer, holdersLocalTotalArea


def closer(self, layer, feedback, seeds=None, totalAreas=None, context=None):
    """
    DESCRIPTION: Function for make swap to get group holder's holdings closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
    INPUTS:
            layer: QgsVectorLayer
            feedback: QgsProcessingMultiStepFeedback
            seeds: List, holding ids
    OUTPUTS: QgsVectorLayer
    """
    #import ptvsd
    #ptvsd.debug_this_thread()
    maxCombTurn = 2000
    changes = 1
    changer = True
    self.globalChangables = vgle_utils.getChangableHoldings(self)

    if seeds:
        self.seeds = seeds
        turn = int(self.actualHolderAttribute.split('_')[0])
        if self.algorithmIndex == 2:
            if turn == (self.steps/2)-3:
                self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
            else:
                if turn >= 10:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                else:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                turn -= 1
    else:
        turn = 0

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Closer algorithm started')

    while changer:
        turn += 1
        self.actualIdAttribute, self.actualHolderAttribute, layer = vgle_layers.setTurnAttributes(self, layer, turn)
        localHoldersWithHoldings = copy.deepcopy(self.holdersWithHoldings)
        localChangables = copy.deepcopy(self.globalChangables)
        feedback.pushInfo(f'Turn {turn}')
        for holder, holdings in localHoldersWithHoldings.items():
            if holder == 'NULL':
                continue

            seedList = self.seeds[holder]

            if len(seedList) >= 1:
                seed = seedList[0]
            else:
                continue

            holderTotalArea = holdersLocalTotalArea[holder]
            # List of all holdings, with distance to the current seed
            inDistance = self.filteredDistanceMatrix[seed]
            # List of holdings, which are not seeds, with distance to the current seed
            distanceChanges = vgle_utils.getChangableHoldings(self, inDistance)
            # List of holder's holdings, which are suitable for change
            filteredHolderHoldingsIds = vgle_utils.idsForChange(holdings, distanceChanges)
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, localChangables)
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, self.holdersWithHoldings[holder])

            sortedDistances = [(y, x) for y, x in zip(list(inDistance.values()), list(inDistance.keys())) if x in filteredHolderHoldingsIds]
            sortedDistances.sort()
            filteredHolderHoldingsIds = [key for value, key in sortedDistances[:5]]
            minAreaHolding = min([self.holdingsWithArea[hold] for hold in holdings])

            tempHolderCombination = None
            tempTargetCombination = None
            tempHolderTotalArea = None
            tempTargetTotalArea = None
            targetHolder = None
            measure = None

            filteredLocalChangables = []
            for distance in distanceChanges:
                if distance not in filteredHolderHoldingsIds and distance in localChangables:
                    filteredLocalChangables.append(distance)

            targetHolders = []
            for changable in filteredLocalChangables:
                for allHolder, allHoldings in self.holdersWithHoldings.items():
                    if changable in allHoldings and allHolder not in targetHolders and allHolder != 'NULL' and holdersLocalTotalArea[allHolder] > minAreaHolding:
                        targetHolders.append(allHolder)
            
            if self.simply:
                if len(targetHolders) > 50:
                    targetHolders = random.choices(targetHolders, k=50)

            for tempTargetHolder in targetHolders:
                targetHolderSeed = self.seeds[tempTargetHolder]
                
                if not targetHolderSeed:
                    if self.useSingle:
                        if self.onlySelected:
                            if tempTargetHolder not in self.selectedHolders:
                                continue
                        else:
                            filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables]
                    else:
                        continue

                targetHolderSeed = self.seeds[tempTargetHolder][0]
                filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                hold in filteredLocalChangables and hold != targetHolderSeed]

                goodCombinations = []
                combTurn = 0
                for sortedCombination in vgle_utils.combine_with_constant_in_all(filteredLocalTargetHoldings):
                    if self.simply:
                        if combTurn < maxCombTurn:
                            combTurn += 1
                        else:
                            break

                    for holderCombination in vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds):
                        targetMaxDistance = vgle_features.maxDistance(self, sortedCombination, targetHolderSeed, layer)
                        holderMaxDistance = vgle_features.maxDistance(self, holderCombination, seed, layer)
                        targetCloser = vgle_utils.isCloser(self, holderMaxDistance, sortedCombination, seed, holder, layer)
                        holderCloser = vgle_utils.isCloser(self, targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder, layer)
                        if not targetCloser or not holderCloser:
                            continue

                        targetAvgDistanceOld = vgle_features.avgDistance(self, sortedCombination, targetHolderSeed, layer)
                        holderAvgDistanceOld = vgle_features.avgDistance(self, holderCombination, seed, layer)
                        holderAvgDistanceNew = vgle_features.avgDistance(self, sortedCombination, seed, layer)
                        targetAvgDistanceNew = vgle_features.avgDistance(self, holderCombination, targetHolderSeed, layer)                           
                        if (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                            goodCombinations.append((holderCombination, sortedCombination))                                   

                if goodCombinations:
                    for holderCombination, targetCombination in goodCombinations:
                        newHolderTotalArea = holderTotalArea - vgle_utils.calculateCombinationArea(self, holderCombination) + vgle_utils.calculateCombinationArea(self, targetCombination)
                        if not vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder):
                            continue
                        newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - vgle_utils.calculateCombinationArea(self, targetCombination) + vgle_utils.calculateCombinationArea(self, holderCombination)
                        if not vgle_utils.checkTotalAreaThreshold(self, newTargetTotalArea, tempTargetHolder):
                            continue
                        localMeasure = sum([vgle_utils.calculateCompositeNumber(self, seed, tempId) for tempId in holderCombination])
                        holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(holderCombination) + len(targetCombination)
                        targetNewHoldingNum = self.holdersHoldingNumber[tempTargetHolder] - len(targetCombination) + len(holderCombination)
                        if holderNewHoldignNum <= self.holdersHoldingNumber[holder] and targetNewHoldingNum <= self.holdersHoldingNumber[tempTargetHolder]:
                            if not measure:
                                targetHolder = copy.copy(tempTargetHolder)
                                tempHolderCombination = copy.copy(holderCombination)
                                tempTargetCombination = copy.copy(targetCombination)
                                measure = copy.copy(localMeasure)
                                tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                tempTargetTotalArea = copy.copy(newTargetTotalArea)
                            else:
                                if measure < localMeasure:
                                    targetHolder = copy.copy(tempTargetHolder)
                                    tempHolderCombination = copy.copy(holderCombination)
                                    tempTargetCombination = copy.copy(targetCombination)
                                    measure = copy.copy(localMeasure)
                                    tempHolderTotalArea = copy.copy(newHolderTotalArea)
                                    tempTargetTotalArea = copy.copy(newTargetTotalArea)

            if measure:
                targetHolderSeed = self.seeds[targetHolder]
                if len(targetHolderSeed) > 0:
                    targetHolderSeed = self.seeds[targetHolder][0]
                elif len(targetHolderSeed) == 0:
                    if self.useSingle:
                        targetHolderSeed = filteredLocalTargetHoldings[0]
                    else:
                        targetHolderSeed = None
                    
                if targetHolderSeed:
                    tempHolderCombination = list(tempHolderCombination)
                    tempTargetCombination = list(tempTargetCombination)
                    vgle_layers.setAttributeValues(self, layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                    if self.stats:
                        self.interactionTable[holder][targetHolder] += 1
                        self.interactionTable[targetHolder][holder] += 1
                    
                    vgle_utils.update_areas_and_distances(self, holder, targetHolder, tempHolderCombination, tempTargetCombination, seed, targetHolderSeed, localChangables, layer)
                    commitMessage = f'Change {self.counter} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): ' \
                                    f'{tempHolderCombination} for {tempTargetCombination}'
                    logging.debug(commitMessage)
                    feedback.pushInfo(commitMessage)

                    holdersLocalTotalArea[holder] = tempHolderTotalArea
                    holdersLocalTotalArea[targetHolder] = tempTargetTotalArea    

        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return None, None
        if turn == 1:
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            if self.counter == 0:
                changer = False
                return None, None
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        else:
            logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
            if changes == self.counter:
                changer = False
                if self.algorithmIndex != 3:
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                    indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                    layer.commitChanges()
            elif (self.algorithmIndex == 1 or self.algorithmIndex == 2) and (turn == self.steps-3):
                vgle_features.filterTouchingFeatures(self, layer)
                changer = False
            elif self.algorithmIndex == 3 and turn == (self.steps/2)-3:
                vgle_features.filterTouchingFeatures(self, layer)
                changer = False
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        feedback.setCurrentStep(1+turn)
        feedback.pushInfo('Save turn results to the file')

    return layer, holdersLocalTotalArea


def closer_multi(self, layer, feedback, seeds=None, totalAreas=None):
    """
    DESCRIPTION: Function for make swap to get group holder's holdings closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
    INPUTS:
            layer: QgsVectorLayer
            feedback: QgsProcessingMultiStepFeedback
            seeds: List, holding ids
    OUTPUTS: QgsVectorLayer
    """
    #import ptvsd
    #ptvsd.debug_this_thread()
    maxCombTurn = 2000
    TIMEOUT_SECONDS = 60
    MAX_PARALLEL = os.cpu_count() - 2
    changes = 1
    changer = True
    self.globalChangables = vgle_utils.getChangableHoldings(self)

    if seeds:
        self.seeds = seeds
        turn = int(self.actualHolderAttribute.split('_')[0])
        if self.algorithmIndex == 2:
            if turn == (self.steps/2)-3:
                self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])) + self.actualHolderAttribute[2:]
                self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])) + self.actualIdAttribute[2:]
            else:
                if turn >= 10:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[2:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[2:]
                else:
                    self.actualHolderAttribute = str(int(self.actualHolderAttribute.split('_')[0])-1) + self.actualHolderAttribute[1:]
                    self.actualIdAttribute = str(int(self.actualIdAttribute.split('_')[0])-1) + self.actualIdAttribute[1:]
                turn -= 1
    else:
        turn = 0

    if totalAreas:
        holdersLocalTotalArea = totalAreas
    else:
        holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)
    feedback.pushInfo('Closer algorithm started')

    while changer:
        turn += 1
        self.actualIdAttribute, self.actualHolderAttribute, layer = vgle_layers.setTurnAttributes(self, layer, turn)
        localHoldersWithHoldings = copy.deepcopy(self.holdersWithHoldings)
        localChangables = copy.deepcopy(self.globalChangables)
        feedback.pushInfo(f'Turn {turn}')
        for holder, holdings in localHoldersWithHoldings.items():
            if holder == 'NULL':
                continue

            seedList = self.seeds[holder]

            if len(seedList) >= 1:
                seed = seedList[0]
            else:
                continue

            holderTotalArea = holdersLocalTotalArea[holder]
            # List of all holdings, with distance to the current seed
            inDistance = self.filteredDistanceMatrix[seed]
            # List of holdings, which are not seeds, with distance to the current seed
            distanceChanges = vgle_utils.getChangableHoldings(self, inDistance)
            # List of holder's holdings, which are suitable for change
            filteredHolderHoldingsIds = vgle_utils.idsForChange(holdings, distanceChanges)
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, localChangables)
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, self.holdersWithHoldings[holder])

            sortedDistances = [(y, x) for y, x in zip(list(inDistance.values()), list(inDistance.keys())) if x in filteredHolderHoldingsIds]
            sortedDistances.sort()
            filteredHolderHoldingsIds = [key for value, key in sortedDistances[:5]]
            minAreaHolding = min([self.holdingsWithArea[hold] for hold in holdings])

            holderCombinationForChange = None
            targetCombinationForChange = None
            holderNewTotalArea = None
            targetNewTotalArea = None
            targetHolder = None
            diff = None

            filteredLocalChangables = []
            for distance in distanceChanges:
                if distance not in filteredHolderHoldingsIds and distance in localChangables:
                    filteredLocalChangables.append(distance)

            targetHolders = []
            for changable in filteredLocalChangables:
                for allHolder, allHoldings in self.holdersWithHoldings.items():
                    if changable in allHoldings and allHolder not in targetHolders and allHolder != 'NULL' and holdersLocalTotalArea[allHolder] > minAreaHolding:
                        targetHolders.append(allHolder)
            
            if self.simply:
                if len(targetHolders) > 50:
                    targetHolders = random.choices(targetHolders, k=50)

            for t, tempTargetHolder in enumerate(targetHolders):
                targetHolderSeed = self.seeds[tempTargetHolder]
                
                if not targetHolderSeed:
                    if self.useSingle:
                        if self.onlySelected:
                            if tempTargetHolder not in self.selectedHolders:
                                continue
                        else:
                            filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables]
                    else:
                        continue
                else:
                    if len(targetHolderSeed) > 0:
                        targetHolderSeed = self.seeds[tempTargetHolder][0]

                targetTotalArea = holdersLocalTotalArea[tempTargetHolder]
                filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                hold in filteredLocalChangables and hold != targetHolderSeed]
                
                #self = vgle_utils.extendDistanceMatrix(self, layer, filteredHolderHoldingsIds, filteredLocalTargetHoldings)     

                def on_task_finished(success, result):
                    nonlocal finished_count, tasks_to_complete, loop, activeTasks, taskResults
                    finished_count += 1
                    if success and result is not None:
                        taskResults.append(result)
                    else:
                        if result:
                            feedback.pushInfo(result)

                    if finished_count >= tasks_to_complete:
                        for task in activeTasks:
                            try:
                                task.cancel()
                            except RuntimeError:
                                pass

                        QCoreApplication.processEvents()
                        if loop.isRunning():
                            loop.quit()

                def cancel_all_tasks_and_quit():
                    nonlocal activeTasks, loop
                    for task in activeTasks:
                        try:
                            task.cancel()
                        except RuntimeError:
                            pass

                    QCoreApplication.processEvents()
                    if loop.isRunning():
                        loop.quit()   


                lenTurn = 0
                for targetCombination in vgle_utils.combine_with_constant_in_all(filteredLocalTargetHoldings):
                    lenTurn += 1
                    if self.simply:
                        if lenTurn >= maxCombTurn:
                            break

                    for i, holderCombination in enumerate(vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds)):
                        app = QgsApplication.instance()
                        task_manager = app.taskManager()
                        app.setMaxThreads(MAX_PARALLEL)
                        finished_count = 0
                        tasks_to_complete = 0
                        activeTasks = []
                        taskResults = []   

                        if self.simply:
                            if i >= maxCombTurn:
                                break
                        task = vgle_multi.CloserFunctionComparisonTask(f'Eval_{t}_{i}', holder, tempTargetHolder, holderCombination, targetCombination, seed, targetHolderSeed, holderTotalArea, targetTotalArea, self, self.strict, self.useSingle, on_finished=on_task_finished)
                        task_manager.addTask(task)
                        activeTasks.append(task)

                loop = QEventLoop()
                tasks_to_complete = len(activeTasks)
                #batch_timer = QTimer()
                #batch_timer.setInterval(TIMEOUT_SECONDS * 1000)
                #batch_timer.setSingleShot(True)
                #batch_timer.timeout.connect(cancel_all_tasks_and_quit)
                
                if tasks_to_complete > 0:
                    QCoreApplication.processEvents()
                    #batch_timer.start()
                    loop.exec_()

                    #if batch_timer.isActive():
                    #    batch_timer.stop()
                    
                for taskResult in taskResults:
                    if diff is not None and taskResult[5] < diff:
                        holderCombinationForChange, targetCombinationForChange, holderNewTotalArea, targetNewTotalArea, targetHolder, diff = taskResult
                    else:
                        if diff is None:
                            holderCombinationForChange, targetCombinationForChange, holderNewTotalArea, targetNewTotalArea, targetHolder, diff = taskResult

            if diff is not None:
                targetHolderSeed = self.seeds[targetHolder]
                if len(targetHolderSeed) > 0:
                    targetHolderSeed = targetHolderSeed[0]
                elif len(targetHolderSeed) == 0:
                    if self.useSingle:
                        targetHolderSeed = filteredLocalTargetHoldings[0]
                    else:
                        targetHolderSeed = None
                    
                if targetHolderSeed:
                    vgle_layers.setAttributeValues(self, layer, holder, targetHolder, holderCombinationForChange, targetCombinationForChange)
                    if self.stats:
                        self.interactionTable[holder][targetHolder] += 1
                        self.interactionTable[targetHolder][holder] += 1
                    
                    vgle_utils.update_areas_and_distances(self, holder, targetHolder, holderCombinationForChange, targetCombinationForChange, seed, targetHolderSeed, localChangables, layer)
                    commitMessage = f'Change {self.counter} for {targetCombinationForChange} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): ' \
                                    f'{holderCombinationForChange} for {targetCombinationForChange}'
                    logging.debug(commitMessage)
                    feedback.pushInfo(commitMessage)

                    holdersLocalTotalArea[holder] = holderNewTotalArea
                    holdersLocalTotalArea[targetHolder] = targetNewTotalArea
                del holderCombinationForChange, targetCombinationForChange, holderNewTotalArea, targetNewTotalArea, diff    

        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return None, None
        if turn == 1:
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            if self.counter == 0:
                changer = False
                return None, None
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        else:
            logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
            if changes == self.counter:
                changer = False
                if self.algorithmIndex != 3:
                    layer.startEditing()
                    indexes = []
                    indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                    indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                    layer.deleteAttributes(indexes)
                    layer.updateFields()
                    layer.commitChanges()
            elif (self.algorithmIndex == 1 or self.algorithmIndex == 2) and (turn == self.steps-3):
                vgle_features.filterTouchingFeatures(self, layer)
                changer = False
            elif self.algorithmIndex == 3 and turn == (self.steps/2)-3:
                vgle_features.filterTouchingFeatures(self, layer)
                changer = False
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        feedback.setCurrentStep(1+turn)
        feedback.pushInfo('Save turn results to the file')

    return layer, holdersLocalTotalArea


def hybrid_method(self, layer, feedback):
    """
    DESCRIPTION: Function for make swap to get group holder's holdings neighbours or closer to their seed polygons. The function try to swap the nearby polygons for other holdings of the holder. 
    INPUTS:
            layer: QgsVectorLayer
            feedback: QgsProcessingMultiStepFeedback
            seeds: List, holding ids
    OUTPUTS: QgsVectorLayer
    """
    # import ptvsd
    # ptvsd.debug_this_thread()
    MAX_TURN = 200000
    changes = 1
    turn = 0
    changer = True
    self.globalChangables = vgle_utils.getChangableHoldings(self)
    holdersLocalTotalArea = copy.deepcopy(self.holdersTotalArea)

    feedback.pushInfo('Hybrid algorithm started')

    while changer:
        turn += 1
        self.actualIdAttribute, self.actualHolderAttribute, layer = vgle_layers.setTurnAttributes(self, layer, turn)
        localChangables = copy.deepcopy(self.globalChangables)
        feedback.pushInfo(f'Turn {turn}')

        turnHolders = list(self.holdersWithHoldings.keys())
        turnHolders = [holder for holder in turnHolders if len(self.seeds[holder]) > 0 and holder != 'NULL']
        if not turnHolders:
            break

        holder = random.choice(turnHolders)

        while holder:
            holdings = self.holdersWithHoldings[holder]
            holderTotalArea = holdersLocalTotalArea[holder]
            seedList = self.seeds[holder]
            seed = seedList[0]

            # List of all holdings, with distance to the current seed
            inDistance = self.filteredDistanceMatrix[seed]
            # List of holdings, which are not seeds, with distance to the current seed
            distanceChanges = vgle_utils.getChangableHoldings(self, inDistance)
            # List of holder's holdings, which are in distance
            filteredHolderHoldingsIds = vgle_utils.idsForChange(holdings, distanceChanges)
            # Filter holder's holdings, which are suitable for change
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, localChangables)
            # Security check, if the holdings still is the holder property (not changed during loop)
            filteredHolderHoldingsIds = vgle_utils.idsForChange(filteredHolderHoldingsIds, self.holdersWithHoldings[holder])
            # Check if the holdings are closest to the certain seed
            #filteredHolderHoldingsIds = self.holdingsClosestToSeed(filteredHolderHoldingsIds, seed, seedList)

            sortedDistances = [(y, x) for y, x in zip(list(inDistance.values()), list(inDistance.keys())) if x in filteredHolderHoldingsIds]
            sortedDistances.sort()
            filteredHolderHoldingsIds = [key for value, key in sortedDistances[:5]]
            holderHoldingsCombinations = vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds)

            minAreaHolding = min([self.holdingsWithArea[hold] for hold in holdings])*((100-self.tolerance)/100)

            neighboursIds, neighboursLayer = vgle_features.getNeighbours(self.idAttribute, layer, seed)
            neighboursHolders = list(set([neighboursFeature.attribute(self.actualHolderAttribute) for neighboursFeature in neighboursLayer.getFeatures()]))
            del neighboursIds, neighboursLayer

            targetHolders = set(neighboursHolders)
            for changable in distanceChanges:
                if changable not in filteredHolderHoldingsIds and changable in localChangables:
                    for allHolder, allHoldings in self.holdersWithHoldings.items():
                        if (
                            changable in allHoldings
                            and allHolder not in targetHolders
                            and allHolder != 'NULL'
                            and holdersLocalTotalArea[allHolder] > minAreaHolding
                        ):
                            targetHolders.add(allHolder)
            del changable, allHolder, allHoldings, minAreaHolding

            bestCombination = None
            for tempTargetHolder in targetHolders:
                targetHolderSeed = self.seeds[tempTargetHolder][0] if self.seeds[tempTargetHolder] else None
                filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                hold in localChangables and 
                                hold not in filteredHolderHoldingsIds and
                                hold in distanceChanges]
                if targetHolderSeed:
                    filteredLocalTargetHoldings = [hold for hold in filteredLocalTargetHoldings if
                                hold  != targetHolderSeed]

                targetAllCombinations = vgle_utils.combine_with_constant_in_all(filteredLocalTargetHoldings)
                
                combTurn = 0
                for targetCombination in targetAllCombinations:
                    for holderCombination in holderHoldingsCombinations:
                        if combTurn < MAX_TURN:
                            combTurn += 1
                        else:
                            break

                        # Maximum distance check
                        targetMaxDistance = vgle_features.maxDistance(self, targetCombination, targetHolderSeed, layer)
                        holderMaxDistance = vgle_features.maxDistance(self, holderCombination, seed, layer)

                        if not (vgle_utils.isCloser(self, holderMaxDistance, targetCombination, seed, holder, layer) and
                                vgle_utils.isCloser(self, targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder, layer)):
                            continue

                        # Average distance check
                        targetAvgDistanceOld = vgle_features.avgDistance(self, targetCombination, targetHolderSeed, layer)
                        holderAvgDistanceOld = vgle_features.avgDistance(self, holderCombination, seed, layer)
                        holderAvgDistanceNew = vgle_features.avgDistance(self, targetCombination, seed, layer)
                        targetAvgDistanceNew = vgle_features.avgDistance(self, holderCombination, targetHolderSeed, layer)

                        if not (targetAvgDistanceNew < targetAvgDistanceOld) and not (holderAvgDistanceNew < holderAvgDistanceOld):
                            continue

                        # Total area check
                        newHolderTotalArea = holderTotalArea - vgle_utils.calculateCombinationArea(self, holderCombination) + vgle_utils.calculateCombinationArea(self, targetCombination)
                        newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - vgle_utils.calculateCombinationArea(self, targetCombination) + vgle_utils.calculateCombinationArea(self, holderCombination)

                        if not (vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder) and
                                vgle_utils.checkTotalAreaThreshold(self, newTargetTotalArea, tempTargetHolder)):
                            continue

                        #Create composite number for ranking
                        score = sum([vgle_utils.calculateCompositeNumber(self, seed, tempId) for tempId in holderCombination])

                        if not bestCombination or score > bestCombination[0]:
                            # Shape check
                            #if not vgle_features.checkShape(self, layer, seed, holdings, holderCombination, targetCombination):
                            #    continue
                            bestCombination = (copy.copy(score),
                                               copy.copy(tempTargetHolder),
                                               copy.copy(targetCombination),
                                               copy.copy(holderCombination),
                                               copy.copy(newHolderTotalArea),
                                               copy.copy(newTargetTotalArea))
                        if feedback.isCanceled():
                            vgle_utils.endLogging() 
                            return None, None

            if bestCombination:
                _, targetHolder, tempTargetCombination, tempHolderCombination, tempHolderTotalArea, tempTargetTotalArea = bestCombination
                targetSeed = self.seeds[targetHolder][0] if self.seeds[targetHolder] else seed
                vgle_layers.setAttributeValues(self, layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                if self.stats:
                    self.interactionTable[holder][targetHolder] += 1
                    self.interactionTable[targetHolder][holder] += 1

                vgle_utils.update_areas_and_distances(self, holder, targetHolder, tempHolderCombination, tempTargetCombination, seed, targetSeed, localChangables)
                commitMessage = f'Change {self.counter} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): ' \
                                f'{tempHolderCombination} for {tempTargetCombination}'
                logging.debug(commitMessage)
                feedback.pushInfo(commitMessage)

                holdersLocalTotalArea[holder] = tempHolderTotalArea
                holdersLocalTotalArea[targetHolder] = tempTargetTotalArea

                turnHolders.remove(holder)
                holder = copy.copy(targetHolder) if targetHolder in turnHolders else (random.choice(turnHolders) if turnHolders else None)
            else:
                turnHolders.remove(holder)
                holder = random.choice(turnHolders) if turnHolders else None

            if feedback.isCanceled():
                vgle_utils.endLogging()
                return None, None

        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return None, None
        if turn == 1:
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            if self.counter == 0:
                changer = False
                return None, None
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        else:
            logging.debug(f'Changes in turn {turn}: {self.counter - changes}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter - changes}')
            if changes == self.counter:
                changer = False
                layer.startEditing()
                indexes = []
                indexes.append(layer.fields().indexFromName(self.actualIdAttribute))
                indexes.append(layer.fields().indexFromName(self.actualHolderAttribute))
                layer.deleteAttributes(indexes)
                layer.updateFields()
            else:
                changes = copy.deepcopy(self.counter)
                vgle_features.filterTouchingFeatures(self, layer)
        feedback.setCurrentStep(1+turn)
        feedback.pushInfo('Save turn results to the file')


    return layer
