-- =============================================================================
-- Analytical Query Workload for Retail Data (DuckDB + Iceberg)
-- =============================================================================
-- 5B+ rows across 3 databases / 6 schemas / 17 tables
-- DuckDB SQL dialect, three-part names: database.schema.table
-- Auto-appends LIMIT 100000 unless user specifies one
-- =============================================================================


-- =============================================================================
-- 1. SIMPLE LOOKUPS
-- =============================================================================

-- 1a. Row counts per table
SELECT 'categories' AS table_name, COUNT(*) AS row_count FROM retail_catalog.products.categories
UNION ALL SELECT 'brands', COUNT(*) FROM retail_catalog.products.brands
UNION ALL SELECT 'suppliers', COUNT(*) FROM retail_catalog.products.suppliers
UNION ALL SELECT 'products', COUNT(*) FROM retail_catalog.products.products
UNION ALL SELECT 'price_lists', COUNT(*) FROM retail_catalog.pricing.price_lists
UNION ALL SELECT 'promotions', COUNT(*) FROM retail_catalog.pricing.promotions
UNION ALL SELECT 'promotion_products', COUNT(*) FROM retail_catalog.pricing.promotion_products
UNION ALL SELECT 'regions', COUNT(*) FROM retail_ops.stores.regions
UNION ALL SELECT 'stores', COUNT(*) FROM retail_ops.stores.stores
UNION ALL SELECT 'employees', COUNT(*) FROM retail_ops.stores.employees
UNION ALL SELECT 'stock_levels', COUNT(*) FROM retail_ops.inventory.stock_levels
UNION ALL SELECT 'replenishment_orders', COUNT(*) FROM retail_ops.inventory.replenishment_orders
UNION ALL SELECT 'orders', COUNT(*) FROM retail_sales.transactions.orders
UNION ALL SELECT 'order_lines', COUNT(*) FROM retail_sales.transactions.order_lines
UNION ALL SELECT 'payments', COUNT(*) FROM retail_sales.transactions.payments
UNION ALL SELECT 'customers', COUNT(*) FROM retail_sales.customers.customers
UNION ALL SELECT 'loyalty_accounts', COUNT(*) FROM retail_sales.customers.loyalty_accounts;

-- 1b. Sample rows from key tables
SELECT * FROM retail_sales.transactions.orders LIMIT 20;

SELECT * FROM retail_sales.transactions.order_lines LIMIT 20;

SELECT * FROM retail_catalog.products.products LIMIT 20;

SELECT * FROM retail_ops.stores.stores LIMIT 20;

SELECT * FROM retail_sales.customers.customers LIMIT 20;

-- 1c. Distinct values and cardinality checks
SELECT
    COUNT(DISTINCT channel) AS channels,
    COUNT(DISTINCT currency) AS currencies,
    MIN(order_date) AS earliest_order,
    MAX(order_date) AS latest_order
FROM retail_sales.transactions.orders;

SELECT COUNT(DISTINCT method) AS payment_methods
FROM retail_sales.transactions.payments;

SELECT tier, COUNT(*) AS accounts
FROM retail_sales.customers.loyalty_accounts
GROUP BY tier
ORDER BY accounts DESC;

SELECT store_type, COUNT(*) AS store_count
FROM retail_ops.stores.stores
GROUP BY store_type;


-- =============================================================================
-- 2. SALES REPORTING (single-table aggregations on billion-row tables)
-- =============================================================================

-- 2a. Monthly revenue trend over 4 years
SELECT
    DATE_TRUNC('month', order_date) AS month,
    SUM(total_amount) AS revenue,
    COUNT(*) AS order_count
FROM retail_sales.transactions.orders
GROUP BY month
ORDER BY month;

-- 2b. Revenue by channel (POS vs web vs app)
SELECT
    channel,
    COUNT(*) AS order_count,
    SUM(total_amount) AS revenue,
    AVG(total_amount) AS avg_order_value
FROM retail_sales.transactions.orders
GROUP BY channel
ORDER BY revenue DESC;

-- 2c. Revenue by currency
SELECT
    currency,
    COUNT(*) AS order_count,
    SUM(total_amount) AS revenue
FROM retail_sales.transactions.orders
GROUP BY currency
ORDER BY revenue DESC;

-- 2d. Daily order volume distribution (how many orders per day, distribution)
SELECT
    DAYOFWEEK(order_date) AS day_of_week,
    COUNT(*) AS total_orders,
    COUNT(*) / COUNT(DISTINCT order_date) AS avg_daily_orders
FROM retail_sales.transactions.orders
GROUP BY day_of_week
ORDER BY day_of_week;

-- 2e. Average order value by year
SELECT
    YEAR(order_date) AS yr,
    COUNT(*) AS order_count,
    AVG(total_amount) AS avg_order_value,
    MEDIAN(total_amount) AS median_order_value
FROM retail_sales.transactions.orders
GROUP BY yr
ORDER BY yr;


-- =============================================================================
-- 3. PRODUCT ANALYTICS (cross-database joins: catalog + sales)
-- =============================================================================

-- 3a. Top 20 products by total revenue
SELECT
    p.product_id,
    p.name AS product_name,
    SUM(ol.line_total) AS total_revenue,
    SUM(ol.quantity) AS units_sold
FROM retail_sales.transactions.order_lines ol
JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
GROUP BY p.product_id, p.name
ORDER BY total_revenue DESC
LIMIT 20;

-- 3b. Top categories by units sold
SELECT
    c.name AS category,
    SUM(ol.quantity) AS units_sold,
    SUM(ol.line_total) AS revenue
FROM retail_sales.transactions.order_lines ol
JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
JOIN retail_catalog.products.categories c ON c.category_id = p.category_id
GROUP BY c.name
ORDER BY units_sold DESC
LIMIT 50;

-- [x] -- 3c. Brand performance ranking
SELECT
    b.name AS brand,
    b.country_of_origin,
    COUNT(DISTINCT ol.order_id) AS order_count,
    SUM(ol.quantity) AS units_sold,
    SUM(ol.line_total) AS revenue
FROM retail_sales.transactions.order_lines ol
JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
JOIN retail_catalog.products.brands b ON b.brand_id = p.brand_id
GROUP BY b.name, b.country_of_origin
ORDER BY revenue DESC
LIMIT 50;

-- 3d. Products with highest discount rates
SELECT
    p.name AS product_name,
    SUM(ol.discount_amount) AS total_discount,
    SUM(ol.line_total) AS total_revenue,
    SUM(ol.discount_amount) / NULLIF(SUM(ol.line_total) + SUM(ol.discount_amount), 0) AS discount_rate
FROM retail_sales.transactions.order_lines ol
JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
GROUP BY p.name
HAVING SUM(ol.line_total) > 0
ORDER BY discount_rate DESC
LIMIT 50;

-- 3e. Slow-moving products (lowest sales velocity over last 12 months)
SELECT
    p.product_id,
    p.name AS product_name,
    COALESCE(SUM(ol.quantity), 0) AS units_sold_last_12m,
    COALESCE(SUM(ol.line_total), 0) AS revenue_last_12m
FROM retail_catalog.products.products p
LEFT JOIN retail_sales.transactions.order_lines ol ON ol.product_id = p.product_id
LEFT JOIN retail_sales.transactions.orders o ON o.order_id = ol.order_id
    AND o.order_date >= DATE '2025-01-01'
GROUP BY p.product_id, p.name
ORDER BY units_sold_last_12m ASC
LIMIT 50;


-- =============================================================================
-- 4. STORE PERFORMANCE (cross-database joins: ops + sales)
-- =============================================================================

-- 4a. Revenue per store, ranked
SELECT
    s.store_id,
    s.name AS store_name,
    s.city,
    s.country,
    s.store_type,
    SUM(o.total_amount) AS revenue,
    COUNT(*) AS order_count
FROM retail_sales.transactions.orders o
JOIN retail_ops.stores.stores s ON s.store_id = o.store_id
GROUP BY s.store_id, s.name, s.city, s.country, s.store_type
ORDER BY revenue DESC
LIMIT 50;

-- 4b. Online vs offline store comparison
SELECT
    s.store_type,
    COUNT(DISTINCT s.store_id) AS store_count,
    COUNT(*) AS order_count,
    SUM(o.total_amount) AS revenue,
    SUM(o.total_amount) / COUNT(DISTINCT s.store_id) AS revenue_per_store
FROM retail_sales.transactions.orders o
JOIN retail_ops.stores.stores s ON s.store_id = o.store_id
GROUP BY s.store_type;

-- 4c. Revenue by region and country
SELECT
    r.name AS region,
    r.country,
    COUNT(*) AS order_count,
    SUM(o.total_amount) AS revenue
FROM retail_sales.transactions.orders o
JOIN retail_ops.stores.stores s ON s.store_id = o.store_id
JOIN retail_ops.stores.regions r ON r.region_id = s.region_id
GROUP BY r.name, r.country
ORDER BY revenue DESC;

-- 4d. Store employee productivity (revenue per employee)
SELECT
    s.store_id,
    s.name AS store_name,
    COUNT(DISTINCT e.employee_id) AS employee_count,
    SUM(o.total_amount) AS revenue,
    SUM(o.total_amount) / NULLIF(COUNT(DISTINCT e.employee_id), 0) AS revenue_per_employee
FROM retail_sales.transactions.orders o
JOIN retail_ops.stores.stores s ON s.store_id = o.store_id
JOIN retail_ops.stores.employees e ON e.store_id = s.store_id
GROUP BY s.store_id, s.name
ORDER BY revenue_per_employee DESC
LIMIT 50;

-- 4e. Stores with declining year-over-year revenue
WITH yearly AS (
    SELECT
        o.store_id,
        YEAR(o.order_date) AS yr,
        SUM(o.total_amount) AS revenue
    FROM retail_sales.transactions.orders o
    GROUP BY o.store_id, YEAR(o.order_date)
)
SELECT
    s.name AS store_name,
    cur.yr AS current_year,
    cur.revenue AS current_revenue,
    prev.revenue AS previous_revenue,
    (cur.revenue - prev.revenue) / NULLIF(prev.revenue, 0) * 100 AS yoy_change_pct
FROM yearly cur
JOIN yearly prev ON prev.store_id = cur.store_id AND prev.yr = cur.yr - 1
JOIN retail_ops.stores.stores s ON s.store_id = cur.store_id
WHERE cur.revenue < prev.revenue
ORDER BY yoy_change_pct ASC
LIMIT 50;


-- =============================================================================
-- 5. CUSTOMER ANALYTICS (cross-database joins: customers + sales)
-- =============================================================================

-- 5a. Customer lifetime value distribution
SELECT
    CASE
        WHEN clv < 100 THEN '0-100'
        WHEN clv < 500 THEN '100-500'
        WHEN clv < 1000 THEN '500-1000'
        WHEN clv < 5000 THEN '1000-5000'
        ELSE '5000+'
    END AS clv_bucket,
    COUNT(*) AS customer_count,
    AVG(clv) AS avg_clv
FROM (
    SELECT
        o.customer_id,
        SUM(o.total_amount) AS clv
    FROM retail_sales.transactions.orders o
    GROUP BY o.customer_id
) sub
GROUP BY clv_bucket
ORDER BY clv_bucket;

-- 5b. Revenue by loyalty tier
SELECT
    la.tier,
    COUNT(DISTINCT la.customer_id) AS customers,
    COUNT(*) AS order_count,
    SUM(o.total_amount) AS revenue,
    SUM(o.total_amount) / COUNT(DISTINCT la.customer_id) AS revenue_per_customer
FROM retail_sales.transactions.orders o
JOIN retail_sales.customers.loyalty_accounts la ON la.customer_id = o.customer_id
GROUP BY la.tier
ORDER BY revenue DESC;

-- 5c. Customer cohort analysis by registration year
SELECT
    YEAR(c.registered_at) AS cohort_year,
    YEAR(o.order_date) AS order_year,
    COUNT(DISTINCT c.customer_id) AS active_customers,
    SUM(o.total_amount) AS revenue
FROM retail_sales.transactions.orders o
JOIN retail_sales.customers.customers c ON c.customer_id = o.customer_id
GROUP BY YEAR(c.registered_at), YEAR(o.order_date)
ORDER BY cohort_year, order_year;

-- 5d. Top 100 customers by total spend
SELECT
    c.customer_id,
    c.first_name || ' ' || c.last_name AS customer_name,
    c.country,
    c.city,
    COUNT(*) AS order_count,
    SUM(o.total_amount) AS total_spend
FROM retail_sales.transactions.orders o
JOIN retail_sales.customers.customers c ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.first_name, c.last_name, c.country, c.city
ORDER BY total_spend DESC
LIMIT 100;

-- 5e. Repeat purchase rate
SELECT
    purchase_count,
    COUNT(*) AS customers,
    COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS pct_of_total
FROM (
    SELECT customer_id, COUNT(*) AS purchase_count
    FROM retail_sales.transactions.orders
    GROUP BY customer_id
) sub
GROUP BY purchase_count
ORDER BY purchase_count
LIMIT 50;


-- =============================================================================
-- 6. INVENTORY & SUPPLY CHAIN (ops-focused)
-- =============================================================================

-- 6a. Low stock alerts (quantity on hand below 10)
SELECT
    s.name AS store_name,
    p.name AS product_name,
    sl.quantity_on_hand,
    sl.quantity_reserved,
    sl.last_recount_at
FROM retail_ops.inventory.stock_levels sl
JOIN retail_ops.stores.stores s ON s.store_id = sl.store_id
JOIN retail_catalog.products.products p ON p.product_id = sl.product_id
WHERE sl.quantity_on_hand < 10
ORDER BY sl.quantity_on_hand ASC
LIMIT 100;

-- 6b. Replenishment order fulfillment rate by supplier
SELECT
    sup.name AS supplier_name,
    COUNT(*) AS total_orders,
    SUM(CASE WHEN ro.status = 'received' THEN 1 ELSE 0 END) AS fulfilled,
    SUM(CASE WHEN ro.status = 'received' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS fulfillment_rate_pct
FROM retail_ops.inventory.replenishment_orders ro
JOIN retail_catalog.products.suppliers sup ON sup.supplier_id = ro.supplier_id
GROUP BY sup.name
ORDER BY fulfillment_rate_pct ASC;

-- 6c. Average replenishment lead time by supplier
SELECT
    sup.name AS supplier_name,
    COUNT(*) AS completed_orders,
    AVG(DATE_DIFF('day', ro.ordered_at, ro.received_at)) AS avg_lead_time_days,
    MAX(DATE_DIFF('day', ro.ordered_at, ro.received_at)) AS max_lead_time_days
FROM retail_ops.inventory.replenishment_orders ro
JOIN retail_catalog.products.suppliers sup ON sup.supplier_id = ro.supplier_id
WHERE ro.received_at IS NOT NULL
GROUP BY sup.name
ORDER BY avg_lead_time_days DESC;

-- 6d. Overstock analysis (high on-hand, low sales in last 6 months)
SELECT
    p.name AS product_name,
    s.name AS store_name,
    sl.quantity_on_hand,
    COALESCE(sales.units_sold, 0) AS units_sold_6m
FROM retail_ops.inventory.stock_levels sl
JOIN retail_ops.stores.stores s ON s.store_id = sl.store_id
JOIN retail_catalog.products.products p ON p.product_id = sl.product_id
LEFT JOIN (
    SELECT ol.product_id, o.store_id, SUM(ol.quantity) AS units_sold
    FROM retail_sales.transactions.order_lines ol
    JOIN retail_sales.transactions.orders o ON o.order_id = ol.order_id
    WHERE o.order_date >= DATE '2025-07-01'
    GROUP BY ol.product_id, o.store_id
) sales ON sales.product_id = sl.product_id AND sales.store_id = sl.store_id
WHERE sl.quantity_on_hand > 100
  AND COALESCE(sales.units_sold, 0) < 10
ORDER BY sl.quantity_on_hand DESC
LIMIT 100;


-- =============================================================================
-- 7. HEAVY AGGREGATIONS (stress tests on billions of rows)
-- =============================================================================

-- [x] -- 7a. Revenue by product x store x month (high-cardinality GROUP BY)
SELECT
    ol.product_id,
    o.store_id,
    DATE_TRUNC('month', o.order_date) AS month,
    SUM(ol.line_total) AS revenue,
    SUM(ol.quantity) AS units
FROM retail_sales.transactions.order_lines ol
JOIN retail_sales.transactions.orders o ON o.order_id = ol.order_id
GROUP BY ol.product_id, o.store_id, DATE_TRUNC('month', o.order_date)
ORDER BY revenue DESC
LIMIT 1000;

-- 7b. Payment method trends over time
SELECT
    DATE_TRUNC('month', p.paid_at) AS month,
    p.method,
    COUNT(*) AS payment_count,
    SUM(p.amount) AS total_amount
FROM retail_sales.transactions.payments p
GROUP BY DATE_TRUNC('month', p.paid_at), p.method
ORDER BY month, method;

-- 7c. Hourly order distribution (time-of-day analysis)
SELECT
    HOUR(order_date) AS hour_of_day,
    channel,
    COUNT(*) AS order_count,
    SUM(total_amount) AS revenue
FROM retail_sales.transactions.orders
GROUP BY HOUR(order_date), channel
ORDER BY hour_of_day, channel;

-- 7d. Moving average of daily revenue (30-day window)
WITH daily_rev AS (
    SELECT
        CAST(order_date AS DATE) AS day,
        SUM(total_amount) AS revenue
    FROM retail_sales.transactions.orders
    GROUP BY CAST(order_date AS DATE)
)
SELECT
    day,
    revenue,
    AVG(revenue) OVER (ORDER BY day ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS moving_avg_30d
FROM daily_rev
ORDER BY day;

-- 7e. Year-over-year growth by category
WITH cat_yearly AS (
    SELECT
        c.name AS category,
        YEAR(o.order_date) AS yr,
        SUM(ol.line_total) AS revenue
    FROM retail_sales.transactions.order_lines ol
    JOIN retail_sales.transactions.orders o ON o.order_id = ol.order_id
    JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
    JOIN retail_catalog.products.categories c ON c.category_id = p.category_id
    GROUP BY c.name, YEAR(o.order_date)
)
SELECT
    cur.category,
    cur.yr AS year,
    cur.revenue,
    prev.revenue AS prev_year_revenue,
    (cur.revenue - prev.revenue) / NULLIF(prev.revenue, 0) * 100 AS yoy_growth_pct
FROM cat_yearly cur
JOIN cat_yearly prev ON prev.category = cur.category AND prev.yr = cur.yr - 1
ORDER BY cur.category, cur.yr;


-- =============================================================================
-- 8. COMPLEX MULTI-JOIN QUERIES (cross all 3 databases)
-- =============================================================================

-- [x] -- 8a. Full order detail: order + lines + product + store + customer + payment
SELECT
    o.order_id,
    o.order_date,
    o.channel,
    c.first_name || ' ' || c.last_name AS customer_name,
    s.name AS store_name,
    s.city AS store_city,
    p.name AS product_name,
    b.name AS brand_name,
    ol.quantity,
    ol.unit_price,
    ol.discount_amount,
    ol.line_total,
    pay.method AS payment_method,
    pay.amount AS payment_amount
FROM retail_sales.transactions.orders o
JOIN retail_sales.transactions.order_lines ol ON ol.order_id = o.order_id
JOIN retail_sales.transactions.payments pay ON pay.order_id = o.order_id
JOIN retail_sales.customers.customers c ON c.customer_id = o.customer_id
JOIN retail_ops.stores.stores s ON s.store_id = o.store_id
JOIN retail_catalog.products.products p ON p.product_id = ol.product_id
JOIN retail_catalog.products.brands b ON b.brand_id = p.brand_id
LIMIT 100;

-- 8b. Promotion effectiveness: promoted vs non-promoted product revenue
SELECT
    CASE WHEN pp.promotion_id IS NOT NULL THEN 'promoted' ELSE 'not_promoted' END AS promo_status,
    COUNT(DISTINCT ol.product_id) AS product_count,
    SUM(ol.quantity) AS units_sold,
    SUM(ol.line_total) AS revenue,
    SUM(ol.discount_amount) AS total_discounts
FROM retail_sales.transactions.order_lines ol
JOIN retail_sales.transactions.orders o ON o.order_id = ol.order_id
LEFT JOIN retail_catalog.pricing.promotion_products pp ON pp.product_id = ol.product_id
LEFT JOIN retail_catalog.pricing.promotions pr ON pr.promotion_id = pp.promotion_id
    AND o.order_date BETWEEN pr.start_date AND pr.end_date
GROUP BY promo_status;

-- 8c. Regional supplier performance: supplier x region x fulfillment rate
SELECT
    sup.name AS supplier_name,
    r.name AS region,
    r.country,
    COUNT(*) AS total_orders,
    SUM(CASE WHEN ro.status = 'received' THEN 1 ELSE 0 END) AS fulfilled,
    AVG(CASE WHEN ro.received_at IS NOT NULL
        THEN DATE_DIFF('day', ro.ordered_at, ro.received_at) END) AS avg_lead_time_days
FROM retail_ops.inventory.replenishment_orders ro
JOIN retail_catalog.products.suppliers sup ON sup.supplier_id = ro.supplier_id
JOIN retail_ops.stores.stores s ON s.store_id = ro.store_id
JOIN retail_ops.stores.regions r ON r.region_id = s.region_id
GROUP BY sup.name, r.name, r.country
ORDER BY sup.name, r.name;

-- 8d. Customer segmentation: loyalty tier x channel x avg basket size
SELECT
    la.tier,
    o.channel,
    COUNT(*) AS order_count,
    AVG(o.total_amount) AS avg_basket_size,
    SUM(o.total_amount) AS total_revenue,
    COUNT(DISTINCT o.customer_id) AS unique_customers
FROM retail_sales.transactions.orders o
JOIN retail_sales.customers.loyalty_accounts la ON la.customer_id = o.customer_id
GROUP BY la.tier, o.channel
ORDER BY la.tier, o.channel;
