-- =====================================================
-- CHECK MODEL USAGE & PERFORMANCE
-- =====================================================
-- Run this script to see which models are being used and their performance.
-- Helps decide which old models are safe to delete.

WITH ModelStats AS (
    SELECT 
        model_version,
        region,
        province,
        COUNT(*) as total_predictions,
        MIN(prediction_date) as first_used,
        MAX(prediction_date) as last_used,
        
        -- Tính số ngày không sử dụng
        (CURRENT_DATE - MAX(prediction_date)) as days_inactive,
        
        -- Tính số lần trúng (nếu có dữ liệu verify)
        COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as win_count
    FROM predictions
    WHERE model_version IS NOT NULL
    GROUP BY model_version, region, province
)
SELECT 
    model_version,
    region,
    province,
    total_predictions,
    win_count,
    first_used,
    last_used,
    days_inactive,
    CASE 
        WHEN days_inactive > 30 THEN '⚠️ UNUSED (>30 days)'
        WHEN days_inactive > 14 THEN '⚠️ RISK (>14 days)'
        ELSE '✅ ACTIVE'
    END as status
FROM ModelStats
ORDER BY last_used DESC, region, province;
