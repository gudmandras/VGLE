import itertools
import multiprocessing
import sys
import os

def combinationIterator(iterable, r):
    returnList = []
    for combination in itertools.combinations(iterable, r):
        if len(combination) >= 1 and len(combination) <= 10:
            returnList.append(combination)
    return returnList

def chunkIteration(iterable, r, chunkSize):
    return itertools.islice(combinationIterator(iterable, r), chunkSize)

class MultiProcessCombinator():

    def __init__(self):
        self.cpu_count = os.cpu_count()-2

    def multiProcess(self, iterable, r):
        chunkIterable = chunkIteration(iterable, r, self.cpu_count)
        with multiprocessing.Pool(processes=self.cpu_count) as pool:
            results = pool.map(chunkIteration, chunkIterable)
            pool.close()
            pool.join()
        result = [result.get() for result in results]
        return result

#def parallelProcess(chunkIteratorVar, poolSize):
#    with multiprocessing.Pool(processes=poolSize) as pool:
#        results = [pool.apply_async(chunkIteratorVar) for _ in range(len(chunkIteratorVar))]
#        pool.close()
#        pool.join()
#        return [result.get() for result in results]
#
if __name__ == "__main__":
    worker = MultiProcessCombinator()
    work = worker.multiProcess(sys.argv[1].split(','), int(sys.argv[2]))
    sys.stdout.write(work)
    #return work
#if __name__ == "__main__":
#    pass