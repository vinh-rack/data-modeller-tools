{{ config(materialized='incremental', alias='stg_c_clean') }}
{# synthetic incremental model #}
select
    id as c_id,
    {{ cents_to_dollars('amount') }} as amount
from {{ source('shop', 'raw_c') }}
{% if is_incremental() %}
where updated_at > (select max(updated_at) from {{ this }})
{% endif %}
