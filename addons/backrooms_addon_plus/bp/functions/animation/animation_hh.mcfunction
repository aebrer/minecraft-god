scoreboard objectives add anime_hh dummy
scoreboard players add @e[type=hh:skin_walker] anime_hh 1
scoreboard players set @e[type=hh:skin_walker,scores={anime_hh=20..}] anime_hh 0
playanimation @e[type=hh:skin_walker,scores={anime_hh=1}] animation.skin_walker.walk