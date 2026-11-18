<?xml version="1.0" encoding="UTF-8"?>
<tileset version="1.9" tiledversion="1.9.2" name="project_tileset" tilewidth="16" tileheight="16" tilecount="2" columns="2" class="TilesetMeta">
 <image source="sprite.png" width="16" height="16"/>
 <properties>
  <property name="tilesetTag" value="world-1"/>
 </properties>
 <tile id="0" class="TileMeta">
  <properties>
   <property name="cost" type="int" value="5"/>
  </properties>
  <objectgroup id="1" name="collision">
   <object id="0" class="Character" x="0" y="0" width="8" height="8">
    <properties>
     <property name="health" type="int" value="42"/>
    </properties>
   </object>
  </objectgroup>
 </tile>
 <tile id="1" class="TileMeta"/>
</tileset>
