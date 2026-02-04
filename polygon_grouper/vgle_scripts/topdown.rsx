##Polygon Grouper=group
##Topdown=name
##QgsProcessingParameterFeatureSource|INPUT|Table|5
##Group=Output table

libs <- c("sp", "igraph", "readr", "ggplot2", "terra")
for (lib in libs) {
  if (!require(lib, character.only = TRUE, quietly = TRUE)) {
    stop(paste("Critical Error: R package", lib, "not found. Please install it."))
  }
}

result <- tryCatch({
    if (nrow(INPUT) == 0) stop("The input table is empty.")
    g <- graph_from_data_frame(INPUT, directed = FALSE)
    holder_cluster <- cluster_louvain(g, weights = NULL, resolution = 1)
    V(g)$group <- membership(holder_cluster)
    data.frame(
        holder = V(g)$name, 
        holder_group = as.vector(membership(holder_cluster))
    )
}, error = function(e) {
    message(paste("R Execution Error:", e$message))
    return(NULL) # Return NULL so Group doesn't contain junk data
}, warning = function(w) {
    message(paste("R Warning:", w$message))
    return(NULL)
})

if (!is.null(result)) {
    Group <- result
} else {
    stop("The algorithm failed to generate a group table. Check the QGIS Message Log.")
}
