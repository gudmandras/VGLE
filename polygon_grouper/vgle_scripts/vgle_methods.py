import copy
import itertools
import logging
import random

from . import vgle_utils, vgle_layers, vgle_features


def neighbours(self, layer, feedback, totalAreas=None):
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
    changes = 1
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
                                temporaryHolderArea = vgle_utils.calculateCombinationArea(self, combination)                             
                                if self.strict:
                                    # Distance conditions
                                    if self.useSingle and not targetHolderSeed:
                                        holderMaxDistance = vgle_features.maxDistance(self, combination, seed, layer)
                                        holderAvgDistanceOld = vgle_features.avgDistance(self, combination, seed, layer)
                                        holderAvgDistanceNew = vgle_features.avgDistance(self, neighbourCombination, seed, layer)
                                        targetCloser = True
                                        targetAvgDistanceNew, targetAvgDistanceOld = 1,2
                                        holderCloser = vgle_utils.isCloser(self, targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
                                    else:
                                        targetMaxDistance = vgle_features.maxDistance(self, neighbourCombination, targetHolderSeed, layer)
                                        targetAvgDistanceOld = vgle_features.avgDistance(self, neighbourCombination, targetHolderSeed, layer)
                                        holderMaxDistance = vgle_features.maxDistance(self, combination, seed, layer)
                                        holderAvgDistanceOld = vgle_features.avgDistance(self, combination, seed, layer)
                                        holderAvgDistanceNew = vgle_features.avgDistance(self, neighbourCombination, seed, layer)
                                        targetAvgDistanceNew = vgle_features.avgDistance(self, combination, targetHolderSeed, layer)
                                        targetCloser = vgle_utils.isCloser(self, holderMaxDistance, neighbourCombination, seed, holder)
                                        holderCloser = vgle_utils.isCloser(self, targetMaxDistance, combination, targetHolderSeed, neighbourHolder)
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
                                                holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                if self.strict:
                                                    if holderNewHoldignNum > self.holdersHoldingNumber[holder] and targetNewHoldingNum > self.holdersHoldingNumber[neighbourHolder]:
                                                        break
                                                holderCombinationForChange = combination
                                                neighbourCombinationForChange = neighbourCombination
                                                holderNewTotalArea = newHolderTotalArea
                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                totalAreaDifference = difference
                                                combTurn += 1
                                        else:
                                            if thresholdHolder and thresholdNeighbour and difference < totalAreaDifference:
                                                holderNewHoldignNum = self.holdersHoldingNumber[holder] - len(combination) + len(neighbourCombination)
                                                targetNewHoldingNum = self.holdersHoldingNumber[neighbourHolder] - len(neighbourCombination) + len(combination)
                                                if self.strict:
                                                    if holderNewHoldignNum > self.holdersHoldingNumber[holder] and targetNewHoldingNum > self.holdersHoldingNumber[neighbourHolder]:
                                                        break
                                                holderCombinationForChange = combination
                                                neighbourCombinationForChange = neighbourCombination
                                                holderNewTotalArea = newHolderTotalArea
                                                neighbourNewTotalArea = newNeighbourTotalArea
                                                totalAreaDifference = difference
                                                combTurn += 1                               

                        if holderCombinationForChange and neighbourCombinationForChange:
                            vgle_layers.setAttributeValues(self, layer, holder, neighbourHolder, holderCombinationForChange, neighbourCombinationForChange)
                            if self.stats:
                                self.interactionTable[holder][neighbourHolder] += 1
                                self.interactionTable[neighbourHolder][holder] += 1
                            if len(holderCombinationForChange) > 1 and len(neighbourCombinationForChange) > 1:
                                #many to many change
                                for holding in holderCombinationForChange:
                                    localChangables.pop(localChangables.index(holding))
                                    changesIds.append(holding)
                                    self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holding]
                                    self.holdingWithSeedDistance[holding] = self.distanceMatrix[targetHolderSeed][holding]
                                    self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[holding]
                                for ngh in neighbourCombinationForChange:
                                    localChangables.pop(localChangables.index(ngh))
                                    changesIds.append(ngh)
                                    self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[ngh]
                                    self.holdingWithSeedDistance[ngh] = self.distanceMatrix[seed][ngh]
                                    self.totalDistances[holder] += self.holdingWithSeedDistance[ngh]
                                self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                self.seeds[holder].append(neighbourTargetFeatureId)
                                holdersLocalTotalArea[holder] = holderNewTotalArea
                                holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            elif len(holderCombinationForChange) > 1 and len(neighbourCombinationForChange) == 1 or len(holderCombinationForChange) == 1 and len(neighbourCombinationForChange) > 1:
                                #many to one change
                                if len(holderCombinationForChange) > 1:
                                    for hold in holderCombinationForChange:
                                        localChangables.pop(localChangables.index(hold))
                                        changesIds.append(hold)
                                        self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold] 
                                        if targetHolderSeed:
                                            self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                                        else:
                                            self.holdingWithSeedDistance[hold] = self.distanceMatrix[neighbourTargetFeatureId][hold]
                                        if targetHolderSeed:
                                            self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[hold]
                                        else:
                                            if self.useSingle:
                                                try:
                                                    self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[hold]
                                                except KeyError:
                                                    self.totalDistances[neighbourHolder] = 0
                                                    self.totalDistances[neighbourHolder] = self.holdingWithSeedDistance[hold]
                                    localChangables.pop(localChangables.index(neighbourTargetFeatureId))
                                    changesIds.append(neighbourTargetFeatureId)
                                    if targetHolderSeed:
                                        self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                    else:
                                        if self.useSingle:
                                            self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                    self.holdingWithSeedDistance[neighbourTargetFeatureId] = self.distanceMatrix[seed][neighbourTargetFeatureId]
                                    self.totalDistances[holder] += self.holdingWithSeedDistance[neighbourTargetFeatureId]
                                else:
                                    for ngh in neighbourCombinationForChange:
                                        localChangables.pop(localChangables.index(ngh))
                                        changesIds.append(ngh)
                                        self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[ngh] 
                                        self.holdingWithSeedDistance[ngh] = self.distanceMatrix[seed][ngh]
                                        self.totalDistances[holder] += self.holdingWithSeedDistance[ngh]
                                    localChangables.pop(localChangables.index(holderCombinationForChange[0]))
                                    changesIds.append(holderCombinationForChange[0])
                                    self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holderCombinationForChange[0]] 
                                    self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                    self.totalDistances[neighbourHolder] += self.holdingWithSeedDistance[holderCombinationForChange[0]]
                                self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                self.seeds[holder].append(neighbourTargetFeatureId)
                                holdersLocalTotalArea[holder] = holderNewTotalArea
                                holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                            else:
                                #one to one change
                                localChangables.pop(localChangables.index(neighbourTargetFeatureId))
                                localChangables.pop(localChangables.index(holderCombinationForChange[0]))
                                changesIds.append(holderCombinationForChange[0])
                                changesIds.append(neighbourTargetFeatureId)
                                self.globalChangables.pop(self.globalChangables.index(neighbourTargetFeatureId))
                                self.seeds[holder].append(neighbourTargetFeatureId)
                                self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[holderCombinationForChange[0]] + self.distanceMatrix[seed][neighbourTargetFeatureId]
                                if targetHolderSeed:
                                    self.totalDistances[neighbourHolder] = self.totalDistances[neighbourHolder] - self.holdingWithSeedDistance[neighbourTargetFeatureId] + self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                self.holdingWithSeedDistance[neighbourTargetFeatureId] = self.distanceMatrix[seed][neighbourTargetFeatureId]
                                if targetHolderSeed:
                                    self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[targetHolderSeed][holderCombinationForChange[0]]
                                else:
                                    if self.useSingle:
                                        self.holdingWithSeedDistance[holderCombinationForChange[0]] = self.distanceMatrix[neighbourTargetFeatureId][holderCombinationForChange[0]]
                                holdersLocalTotalArea[holder] = holderNewTotalArea
                                holdersLocalTotalArea[neighbourHolder] = neighbourNewTotalArea
                                commitMessage = f'Change {str(self.counter)} for {neighbourTargetFeatureId} (holder:{neighbourHolder}) as neighbour of {seed} (holder:{holder}): {holderCombinationForChange} for {neighbourCombinationForChange}'
                                logging.debug(commitMessage)
                                feedback.pushInfo(commitMessage)
                not_changables.extend(changesIds)

        if turn == 1:
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            if changes == 0:
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
            return {}
    return layer, holdersLocalTotalArea

def closer(self, layer, feedback, seeds=None, totalAreas=None):
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
    maxCombTurn = 20000
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
                if len(targetHolderSeed) > 0:                  
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
                            targetCloser = vgle_utils.isCloser(self, holderMaxDistance, sortedCombination, seed, holder)
                            holderCloser = vgle_utils.isCloser(self, targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder)
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
                else:
                    if self.useSingle:
                        if self.onlySelected:
                            if tempTargetHolder not in self.selectedHolders:
                                continue
                        filteredLocalTargetHoldings = [hold for hold in self.holdersWithHoldings[tempTargetHolder] if
                                                        hold in filteredLocalChangables]
                        
                        goodCombinations = []
                        combTurn = 0
                        for sortedCombination in vgle_utils.combine_with_constant_in_all(filteredLocalTargetHoldings):
                            if self.simply:
                                if combTurn < maxCombTurn:
                                    combTurn += 1
                                else:
                                    break
        
                            for holderCombination in vgle_utils.combine_with_constant_in_all(filteredHolderHoldingsIds):
                                holderMaxDistance = vgle_features.maxDistance(self, holderCombination, seed, layer)
                                targetCloser = vgle_utils.isCloser(self, holderMaxDistance, sortedCombination, seed, holder)
                                if not targetCloser:
                                    continue

                                holderAvgDistanceOld = vgle_features.avgDistance(self, holderCombination, seed, layer)
                                holderAvgDistanceNew = vgle_features.avgDistance(self, sortedCombination, seed, layer)
                                if holderAvgDistanceNew < holderAvgDistanceOld:
                                    goodCombinations.append((holderCombination, sortedCombination))                                    

                        if goodCombinations:
                            for index, goodCombination in goodCombinations:
                                holderCombination, targetCombination = goodCombination
                                newHolderTotalArea = holderTotalArea - vgle_utils.calculateCombinationArea(self, holderCombination) + vgle_utils.calculateCombinationArea(self, targetCombination)
                                if not vgle_utils.checkTotalAreaThreshold(self, newHolderTotalArea, holder):
                                    continue
                                newTargetTotalArea = holdersLocalTotalArea[tempTargetHolder] - vgle_utils.calculateCombinationArea(self, targetCombination) + vgle_utils.calculateCombinationArea(self, holderCombination)
                                if vgle_utils.checkTotalAreaThreshold(self, newTargetTotalArea, tempTargetHolder):
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
                tempHolderCombination = list(tempHolderCombination)
                tempTargetCombination = list(tempTargetCombination)
                vgle_layers.setAttributeValues(self, layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                if self.stats:
                    self.interactionTable[holder][targetHolder] += 1
                    self.interactionTable[targetHolder][holder] += 1
                if len(tempHolderCombination) > 1 and len(tempTargetCombination) > 1:
                    #many to many change
                    for hold in tempHolderCombination:
                        localChangables.pop(localChangables.index(hold))
                        self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold]
                        self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                        self.totalDistances[targetHolder] += self.holdingWithSeedDistance[hold]
                    for ch in tempTargetCombination:
                        localChangables.pop(localChangables.index(ch))
                        self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch]
                        self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                        self.totalDistances[holder] += self.holdingWithSeedDistance[ch]
                    holdersLocalTotalArea[holder] = tempHolderTotalArea
                    holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                    commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination}'
                    logging.debug(commitMessage)
                    feedback.pushInfo(commitMessage)
                elif len(tempHolderCombination) > 1 and len(tempTargetCombination) == 1 or len(tempHolderCombination) == 1 and len(tempTargetCombination) > 1:
                    #many to one change
                    if len(tempHolderCombination) > 1:
                        for hold in tempHolderCombination:
                            localChangables.pop(localChangables.index(hold))
                            self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[hold]
                            if targetHolderSeed:
                                self.holdingWithSeedDistance[hold] = self.distanceMatrix[targetHolderSeed][hold]
                            else:
                                if self.useSingle:
                                    self.holdingWithSeedDistance[hold] = self.distanceMatrix[tempTargetCombination[0]][hold]
                            self.totalDistances[targetHolder] += self.holdingWithSeedDistance[hold]
                        localChangables.pop(localChangables.index(tempTargetCombination[0]))
                        self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]]
                        self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                        self.totalDistances[holder] += self.holdingWithSeedDistance[tempTargetCombination[0]]
                        commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination} for {tempTargetCombination[0]}'
                        logging.debug(commitMessage)
                        feedback.pushInfo(commitMessage)
                    else:
                        for ch in tempTargetCombination:
                            localChangables.pop(localChangables.index(ch))
                            self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[ch] 
                            self.holdingWithSeedDistance[ch] = self.distanceMatrix[seed][ch]
                            self.totalDistances[holder] += self.holdingWithSeedDistance[ch]
                        localChangables.pop(localChangables.index(tempHolderCombination[0]))
                        self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]]
                        self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                        self.totalDistances[targetHolder] += self.holdingWithSeedDistance[tempHolderCombination[0]]
                        commitMessage = f'Change {str(self.counter)} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination}'
                        logging.debug(commitMessage)
                        feedback.pushInfo(commitMessage)
                    holdersLocalTotalArea[holder] = tempHolderTotalArea
                    holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                else:
                    #one to one change
                    localChangables.pop(localChangables.index(tempTargetCombination[0]))
                    localChangables.pop(localChangables.index(tempHolderCombination[0]))
                    self.totalDistances[holder] = self.totalDistances[holder] - self.holdingWithSeedDistance[tempHolderCombination[0]] + self.distanceMatrix[seed][tempTargetCombination[0]]
                    if targetHolderSeed:
                        self.totalDistances[targetHolder] = self.totalDistances[targetHolder] - self.holdingWithSeedDistance[tempTargetCombination[0]] + self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                    self.holdingWithSeedDistance[tempTargetCombination[0]] = self.distanceMatrix[seed][tempTargetCombination[0]]
                    if targetHolderSeed:
                        self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[targetHolderSeed][tempHolderCombination[0]]
                    else:
                        if self.useSingle:
                            self.holdingWithSeedDistance[tempHolderCombination[0]] = self.distanceMatrix[tempTargetCombination[0]][tempHolderCombination[0]]
                    holdersLocalTotalArea[holder] = tempHolderTotalArea
                    holdersLocalTotalArea[targetHolder] = tempTargetTotalArea
                    commitMessage = f'Change {str(self.counter)} for {tempTargetCombination[0]} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): {tempHolderCombination[0]} for {tempTargetCombination[0]}'
                    logging.debug(commitMessage)
                    feedback.pushInfo(commitMessage)

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

                        if not (vgle_utils.isCloser(self, holderMaxDistance, targetCombination, seed, holder) and
                                vgle_utils.isCloser(self, targetMaxDistance, holderCombination, targetHolderSeed, tempTargetHolder)):
                            continue

                        # Average distance check

                        targetAvgDistanceOld = vgle_features.avgDistance(self, targetCombination, targetHolderSeed, layer)
                        holderAvgDistanceOld = vgle_features.avgDistance(self, holderCombination, seed, layer)
                        holderAvgDistanceNew = vgle_features.avgDistance(self, targetCombination, seed, layer)
                        targetAvgDistanceNew = vgle_features.avgDistance(self, holderCombination, targetHolderSeed, layer)

                        if not (targetAvgDistanceNew < targetAvgDistanceOld) and (holderAvgDistanceNew < holderAvgDistanceOld):
                            continue

                        # Shape check
                        if not vgle_features.checkShape(self, layer, seed, holdings, holderCombination, targetCombination):
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
                            bestCombination = (copy.copy(score),
                                               copy.copy(tempTargetHolder),
                                               copy.copy(targetCombination),
                                               copy.copy(holderCombination),
                                               copy.copy(newHolderTotalArea),
                                               copy.copy(newTargetTotalArea))

            if bestCombination:
                _, targetHolder, tempTargetCombination, tempHolderCombination, tempHolderTotalArea, tempTargetTotalArea = bestCombination
                targetSeed = self.seeds[targetHolder][0] if self.seeds[targetHolder] else seed
                vgle_layers.setAttributeValues(self, layer, holder, targetHolder, tempHolderCombination, tempTargetCombination)
                if self.stats:
                    self.interactionTable[holder][targetHolder] += 1
                    self.interactionTable[targetHolder][holder] += 1

                update_areas_and_distances(self, holder, targetHolder, hComb, tComb, seed, targetSeed)
                vgle_utils.commitMessage = f'Change {self.counter} for {tempTargetCombination} (holder:{targetHolder}) to get closer to {seed} (holder:{holder}): ' \
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
                return {}

        if feedback.isCanceled():
            vgle_utils.endLogging() 
            return {}
        if turn == 1:
            logging.debug(f'Changes in turn {turn}: {self.counter}')
            feedback.pushInfo(f'Changes in turn {turn}: {self.counter}') 
            if self.counter == 0:
                changer = False
                return None
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