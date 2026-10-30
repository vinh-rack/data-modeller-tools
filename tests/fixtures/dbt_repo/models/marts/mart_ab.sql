with a as (
    select * from {{ ref('stg_a') }}
),

b as (
    select * from {{ ref('shop_pkg', 'stg_b') }}
),

v as (
    select * from {{ ref('stg_versioned', v=2) }}
)

select a.a_id, b.b_id, v.x_id
from a
join b on a.a_id = b.a_id
cross join v
