scoreboard objectives add level dummy
effect @e[type=hh:smiler] speed infinite 2 true
execute as @a at @s run execute if block ~ ~ ~ hh:entity_spawn_block run function entity_spawn_smiler
execute as @a at @s run execute if block ~ ~ ~ hh:clip2 run function level/clear
execute as @a at @s run execute if block ~ ~ ~ hh:clip3 run function level/lrun
execute as @a[scores={level=0}] at @s run execute if block ~ ~ ~ sand run execute if block ~ ~1 ~ sand run function level/l0
execute as @a[scores={level=0}] at @s run execute if block ~ ~ ~ gravel run execute if block ~ ~1 ~ gravel run function level/l0
execute as @a[scores={level=0}] at @s run execute if block ~ ~ ~ hh:clip run function level/l0
execute as @a[scores={level=1}] at @s run execute if block ~ ~ ~ hh:clip run function level/l1
execute as @a[scores={level=2}] at @s run execute if block ~ ~ ~ hh:clip run function level/l2
execute as @a[scores={level=3}] at @s run execute if block ~ ~ ~ hh:clip run function level/l3
execute as @a[scores={level=4}] at @s run execute if block ~ ~ ~ hh:clip run function level/l4
execute as @a[scores={level=37}] at @s run execute if block ~ ~ ~ hh:clip run function level/l36
execute as @a[scores={level=922337}] at @s run execute if block ~ ~ ~ hh:clip run function level/l360
execute as @a at @s run execute if block ~ ~ ~ hh:clip4 run function level/lfun
execute as @a at @s run execute if block ~ ~ ~ hh:clip5 run function level/l15358
execute as @a at @s run execute if block ~ ~ ~ hh:clip6 run function level/l3999
execute as @a at @s run execute if block ~ ~ ~ hh:entity_spawn_block2 run function entity_spawn_37
execute as @a[scores={level=38}] at @s run execute if block ~ ~ ~ hh:clip run function level/l38