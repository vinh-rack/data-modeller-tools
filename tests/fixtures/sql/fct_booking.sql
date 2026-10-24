with b as (
    select bookingno as booking_num, amount from brnz_fleetops.booking
)
select b.booking_num, sum(b.amount + m.reading) as total_amount
from b
join brnz_fleetops.meter_change m on b.booking_num = m.bookingno
group by b.booking_num
