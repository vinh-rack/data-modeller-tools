select id as b_id, a_id
from {{ source("shop", "raw_b") }}
