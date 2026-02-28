-- Migration: 02_add_weekday_to_model_registry.sql
-- Thêm cột weekday vào model_registry để hỗ trợ model tách theo ngày trong tuần.
-- NULL = model cũ (không phân biệt weekday, dùng làm fallback)
-- 0 = Thứ Hai, 1 = Thứ Ba, ..., 6 = Chủ Nhật

ALTER TABLE public.model_registry
    ADD COLUMN IF NOT EXISTS weekday INT CHECK (weekday >= 0 AND weekday <= 6);

-- Update unique constraint (nếu có) để bao gồm weekday
-- Xóa constraint cũ nếu tồn tại và tạo lại với weekday
DO $$
BEGIN
    -- Drop old unique constraint if exists (name may vary)
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'model_registry_region_province_status_key'
    ) THEN
        ALTER TABLE public.model_registry
            DROP CONSTRAINT model_registry_region_province_status_key;
    END IF;
END $$;

-- Comment
COMMENT ON COLUMN public.model_registry.weekday IS
    'Day of week this model was trained for (0=Mon..6=Sun). NULL = no weekday split (legacy/XSMB).';
