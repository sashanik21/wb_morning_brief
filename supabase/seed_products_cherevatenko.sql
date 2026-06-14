-- Seed active WB products for seller_id = 1 (ИП Череватенко Б.С.).
-- Source: current sales funnel SKU set expected by the morning brief pipeline.
-- Safe to run repeatedly: rows are updated by (seller_id, nm_id).

insert into products (
    seller_id,
    nm_id,
    vendor_code,
    product_name,
    brand,
    abc,
    status
)
values
    (1, 1088430501, 'Пэпэ Кедрус_30ml', 'Масляные духи по мотивам Chloe Cedrus 30 мл', 'МИЗ', 'A', 'active'),
    (1, 1088430502, 'Пэпэ Номад_30ml', 'Масляные духи по мотивам Chloe Nomade 30 мл', 'МИЗ', 'A', 'active'),
    (1, 1088430503, 'Пэпэ Лав_30ml', 'Масляные духи по мотивам Chloe Love 30 мл', 'МИЗ', 'A', 'active'),
    (1, 1088430504, 'Пэпэ Флёр_30ml', 'Масляные духи по мотивам Chloe Fleur 30 мл', 'МИЗ', 'B', 'active'),
    (1, 1088430505, 'Пэпэ Сигнейчур_30ml', 'Масляные духи по мотивам Chloe Signature 30 мл', 'МИЗ', 'B', 'active'),
    (1, 1088430506, 'Пэпэ Розес_30ml', 'Масляные духи по мотивам Chloe Roses 30 мл', 'МИЗ', 'B', 'active'),
    (1, 1088430507, 'Пэпэ Абсолю_30ml', 'Масляные духи по мотивам Chloe Absolu 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430508, 'Пэпэ Натюрель_30ml', 'Масляные духи по мотивам Chloe Naturelle 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430509, 'Пэпэ Люмьер_30ml', 'Масляные духи по мотивам Chloe Lumineuse 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430510, 'Пэпэ Интенс_30ml', 'Масляные духи по мотивам Chloe Intense 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430511, 'Пэпэ Ателье_30ml', 'Масляные духи по мотивам Chloe Atelier 30 мл', 'МИЗ', 'B', 'active'),
    (1, 1088430512, 'Пэпэ Роуз Танжерин_30ml', 'Масляные духи по мотивам Chloe Rose Tangerine 30 мл', 'МИЗ', 'B', 'active'),
    (1, 1088430513, 'Пэпэ Номад Натюрель_30ml', 'Масляные духи по мотивам Chloe Nomade Naturelle 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430514, 'Пэпэ Номад Абсолю_30ml', 'Масляные духи по мотивам Chloe Nomade Absolu 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430515, 'Пэпэ Си Белль_30ml', 'Масляные духи по мотивам Chloe See by Chloe 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430516, 'Пэпэ Л''О_30ml', 'Масляные духи по мотивам Chloe L''Eau 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430517, 'Пэпэ Роуз Натюрель_30ml', 'Масляные духи по мотивам Chloe Rose Naturelle 30 мл', 'МИЗ', 'C', 'active'),
    (1, 1088430518, 'Пэпэ Пудре_30ml', 'Масляные духи по мотивам Chloe Poudree 30 мл', 'МИЗ', 'C', 'active')
on conflict (seller_id, nm_id) do update set
    vendor_code = excluded.vendor_code,
    product_name = excluded.product_name,
    brand = excluded.brand,
    abc = excluded.abc,
    status = excluded.status;
