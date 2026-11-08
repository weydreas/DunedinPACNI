# No patient MRI data are required. We test the published model on synthetic ROI data.
suppressPackageStartupMessages(library(progress))
source("R/ExportDunedinPACNI.R", local = TRUE)
load("data/coefs_dk.rda")
stopifnot(length(coefs_dk) > 1L)
model_roi <- names(coefs_dk)[-1L]
fixture <- as.data.frame(as.list(setNames(rep(1.0, length(model_roi)), model_roi)),
                         check.names = FALSE, stringsAsFactors = FALSE)
fixture$ID <- "synthetic_control"
out_dir <- tempfile("pacni_test_")
dir.create(out_dir)
normal <- ExportDunedinPACNI(fixture, outdir = out_dir, missing_gwr = TRUE)
stopifnot(nrow(normal) == 1L, is.finite(normal$pacni[[1L]]))
required <- model_roi[as.numeric(coefs_dk[-1L]) != 0]
stopifnot(length(required) > 0)
bad <- fixture
bad[[required[[1L]]]] <- NA_real_
failed <- tryCatch({
  ExportDunedinPACNI(bad, outdir = out_dir, missing_gwr = TRUE)
  FALSE
}, error = function(e) TRUE)
stopifnot(failed)
cat("Synthetic ROI calculation passed; missing weighted ROI was rejected.\n")
