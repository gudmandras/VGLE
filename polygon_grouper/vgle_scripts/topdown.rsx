##Polygon Grouper=group
##Topdown=name
##QgsProcessingParameterFeatureSource|INPUT|Table|5
##Group=Output table
##MODULARITY=output number
##showplots

required_packages <- c("sp", "igraph", "readr", "ggplot2", "terra")

for(pkg in required_packages){
  if(!require(pkg, character.only = TRUE)){
    install.packages(pkg, repos = "https://cloud.r-project.org/")
    library(pkg, character.only = TRUE)
  }
}

library(sp)
library(igraph)
library(readr)
library(ggplot2)
library(terra)

g <- graph_from_data_frame(INPUT, directed = FALSE)
holder_cluster <- cluster_louvain(g, weights = NULL, resolution = 1)
V(g)$group <- membership(holder_cluster)
mod_value <- modularity(holder_cluster)
MODULARITY <- mod_value
result_df <- data.frame(holder = V(g)$name, holder_group = membership(holder_cluster))
Group <- result_df