structure load level1 0 120 0
setblock ~ ~ ~ air
tp @a 21 122 23
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 2
kill @e[type=hh:entity_0]
kill @e[type=hh:wormlings]
title @a subtitle §2Level§7{§11§7}
title @a title The Backrooms
setblock 26.70 121.00 37.30 hh:block_1
title @a times 1 60 0
setblock 24.54 121.00 37.45 hh:block_1
summon hh:smiler l1 58.41 122.00 29.95