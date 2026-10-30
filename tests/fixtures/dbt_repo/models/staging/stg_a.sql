select
    id as a_id,
    name as a_nm
from {{ source('shop', 'raw_a') }}
