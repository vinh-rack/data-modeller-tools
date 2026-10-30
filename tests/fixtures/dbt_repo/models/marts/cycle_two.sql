select c1.*, a.id
from {{ ref('cycle_one') }} as c1
join {{ source('shop', 'raw_a') }} as a on c1.id = a.id
