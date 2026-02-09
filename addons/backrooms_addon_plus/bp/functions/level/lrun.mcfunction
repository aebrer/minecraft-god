structure load levelrun 0 120 0
setblock ~ ~ ~ air
tp @a 20 121 34 0
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 37
tag @a remove smoke
summon hh:smiler 20.29 121.00 28.06
setblock 25.46 122.00 18.58 hh:level_run_block2
title @a subtitle §2Level§7{§1Run!!§7}
title @a title The Backrooms
setblock 25 123 18 hh:level_run_block
title @a times 1 60 0