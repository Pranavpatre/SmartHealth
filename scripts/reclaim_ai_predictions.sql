-- One-time reclaim of the bloated ai_predictions table on a near-full disk.
--
-- The 15-min prediction scan never deleted, so the table grew to ~58.6M rows /
-- 19GB and filled the colima data disk. A batched DELETE + VACUUM cannot recover
-- here: it needs temp/WAL space we don't have, and plain VACUUM won't return the
-- 19GB to the OS. Instead we build a small table with only the rows anything
-- depends on, then DROP the bloated one (instant file reclaim, no rewrite/temp).
--
-- Keepers (see prune_ai_predictions task for the same contract):
--   - last 30 min of predicted_at  -> current latest-per-pair (redistribution /
--     facility-detail read DISTINCT ON newest per pair)
--   - actual_value / worker_feedback set -> retraining signal (0 rows today)
--   - referenced by alerts.prediction_id or redistribution_items.trigger_prediction
--     -> FK RESTRICT (12 redist refs, 0 alert refs today)

BEGIN;

CREATE TABLE ai_predictions_new (LIKE ai_predictions INCLUDING ALL);

INSERT INTO ai_predictions_new
SELECT p.*
FROM ai_predictions p
WHERE p.predicted_at >= (SELECT max(predicted_at) FROM ai_predictions) - INTERVAL '30 minutes'
   OR p.actual_value    IS NOT NULL
   OR p.worker_feedback IS NOT NULL
   OR EXISTS (SELECT 1 FROM alerts a              WHERE a.prediction_id      = p.id)
   OR EXISTS (SELECT 1 FROM redistribution_items ri WHERE ri.trigger_prediction = p.id);

-- Inbound FKs reference ai_predictions by name; drop, swap, re-add against the new table.
ALTER TABLE alerts              DROP CONSTRAINT alerts_prediction_id_fkey;
ALTER TABLE redistribution_items DROP CONSTRAINT redistribution_items_trigger_prediction_fkey;

DROP TABLE ai_predictions;                          -- releases the 19GB file
ALTER TABLE ai_predictions_new RENAME TO ai_predictions;

ALTER TABLE alerts
    ADD CONSTRAINT alerts_prediction_id_fkey
    FOREIGN KEY (prediction_id) REFERENCES ai_predictions(id);
ALTER TABLE redistribution_items
    ADD CONSTRAINT redistribution_items_trigger_prediction_fkey
    FOREIGN KEY (trigger_prediction) REFERENCES ai_predictions(id);

COMMIT;
