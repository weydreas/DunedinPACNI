args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("Usage: run_pacni.R /subjects /results TRUE|FALSE")
library(DunedinPACNI)
missing_gwr <- switch(args[[3]], "TRUE" = TRUE, "FALSE" = FALSE,
                      stop("Invalid GWR argument"))
df <- LoadFreeSurferStats(fsdir = args[[1]],
                          sublist = file.path(args[[2]], "sublist.csv"),
                          missing_gwr = missing_gwr)
if (nrow(df) != 1) stop("Expected exactly one MRI study")
scores <- ExportDunedinPACNI(data = df, outdir = args[[2]],
                             missing_gwr = missing_gwr)
if (nrow(scores) != 1 || !is.finite(scores$pacni[[1]])) {
  stop("Invalid DunedinPACNI score")
}
print(scores)
