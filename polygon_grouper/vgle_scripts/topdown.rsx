##Polygon Grouper=group
##Topdown=name
##QgsProcessingParameterFeatureSource|INPUT|Table|5
##Group=Output table

library(sp)
library(igraph)
library(readr)
library(ggplot2)
library(terra)

g <- graph_from_data_frame(INPUT, directed = FALSE)
holder_cluster <- cluster_louvain(g, weights = NULL, resolution = 1)
V(g)$group <- membership(holder_cluster)

Group <- data.frame(
  holder = V(g)$name, 
  holder_group = as.vector(membership(holder_cluster))
)
