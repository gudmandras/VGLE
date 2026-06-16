##Polygon Grouper=group
##Topdown2=name
##QgsProcessingParameterFeatureSource|INPUT|Table|5
##QgsProcessingParameterEnum|OBJECTIVE_FUNCTION|Objective function (Advanced users only)|modularity;CPM|False|0
##QgsProcessingParameterNumber|RESOLUTION|Resolution (Advanced users only)|QgsProcessingParameterNumber.Double|1.0|False|0.0001|2.0
##Group=Output table

libs <- c("sp", "igraph", "readr", "ggplot2", "terra")
for (lib in libs) {
  if (!require(lib, character.only = TRUE, quietly = TRUE)) {
    stop(paste("Critical Error: R package", lib, "not found. Please install it."))
  }
}

objective_function <- c("modularity", "CPM")[OBJECTIVE_FUNCTION + 1]
resolution_value <- RESOLUTION

# Validation according to objective function
if (objective_function == "modularity") {
    if (resolution_value < 0.2 || resolution_value > 2.0) {
        stop("For modularity, resolution must be between 0.2 and 2.0.")
    }
} else {
    if (resolution_value < 0.0001 || resolution_value > 1.0) {
        stop("For CPM, resolution must be between 0.0001 and 1.0.")
    }
}

result <- tryCatch({
    if (nrow(INPUT) == 0) stop("The input table is empty.")
    g <- graph_from_data_frame(INPUT, directed = FALSE)
    holder_cluster <- cluster_leiden(g, objective_function = objective_function, resolution = resolution_value)
    node_groups <- membership(holder_cluster)
    isolated_nodes <- degree(g) == 0
    if (any(isolated_nodes)) {
        max_group <- max(node_groups)
        node_groups[isolated_nodes] <- max_group + 1
    }
    node_groups <- as.numeric(as.factor(node_groups))
    holder_cluster <- make_clusters(
    graph = g,
    membership = node_groups,
    algorithm = "Leiden (Continuous Numbering)"
    )

    data.frame(
        holder = V(g)$name, 
        holder_group = node_groups
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
