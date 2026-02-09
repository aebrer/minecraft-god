structure load level15358 0 120 0
setblock ~ ~ ~ air
tp @a 40.27 121.00 42.58
function hh_smoke
effect @a slowness 1 99 true
scoreboard players set @a level 0
tag @a remove smoke
title @a subtitle §2Level§7{§115358§7}
title @a title The Backrooms
title @a times 1 60 0
kill @e[family=backroom]
fill 60.70 121.00 63.70 4.30 145.94 63.70 white_concrete
structure load player 25 121 21