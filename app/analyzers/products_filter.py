from app.analyzers.products_enrichment import enrich_funnel_data_with_products


def filter_funnel_data_by_products(data, products):
    return enrich_funnel_data_with_products(data, products)
