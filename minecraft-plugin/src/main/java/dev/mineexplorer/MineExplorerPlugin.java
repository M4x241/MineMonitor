package dev.mineexplorer;

import org.bukkit.Bukkit;
import org.bukkit.GameRule;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.WorldCreator;
import org.bukkit.WorldType;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.jetbrains.annotations.NotNull;

import java.util.List;

/**
 * Plugin principal: crea el mundo-mina, genera las galerías por seed,
 * despliega el mob explorador (NoAI) y abre el servidor WebSocket.
 */
public final class MineExplorerPlugin extends JavaPlugin {

    private World world;
    private MineGenerator generator;
    private ExplorerMob explorer;
    private MineWsServer wsServer;

    private int wsPort;
    private String seed;
    private int regionHalf;
    private int startX, startY, startZ;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        loadSettings();

        world = ensureMineWorld();
        generator = new MineGenerator(this, world, seed, regionHalf, startX, startY, startZ);
        generator.generateIfNeeded();
        keepMineLoaded();

        explorer = new ExplorerMob(this, world, generator.getDroneSpawn());
        // Spawn retrasado: espera a que el mundo termine de estabilizarse al arrancar.
        Bukkit.getScheduler().runTaskLater(this, explorer::spawn, 40L);

        wsServer = new MineWsServer(this, wsPort, explorer, world, generator);
        wsServer.start();

        getServer().getPluginManager().registerEvents(new EntityWatchdog(explorer), this);

        getLogger().info("MineExplorer activo. Mina seed=" + seed
                + " región=" + regionHalf + " WS en puerto " + wsPort);
    }

    @Override
    public void onDisable() {
        if (wsServer != null) {
            try {
                wsServer.stop(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        if (explorer != null) {
            explorer.remove();
        }
        getLogger().info("MineExplorer apagado.");
    }

    private void loadSettings() {
        wsPort = getConfig().getInt("ws-port", 8765);
        seed = getConfig().getString("seed", "1704");
        regionHalf = getConfig().getInt("region-half", 48);
        startX = getConfig().getInt("start-x", 0);
        startY = getConfig().getInt("start-y", 54);
        startZ = getConfig().getInt("start-z", 0);
    }

    private World ensureMineWorld() {
        World w = Bukkit.getWorld("mine");
        if (w == null) {
            WorldCreator wc = new WorldCreator("mine");
            wc.type(WorldType.FLAT);
            wc.generatorSettings("minecraft:bedrock,120*minecraft:stone;minecraft:plains");
            wc.environment(World.Environment.NORMAL);
            wc.generateStructures(false);
            w = Bukkit.createWorld(wc);
        }
        w.setTime(1000);
        w.setStorm(false);
        w.setThundering(false);
        w.setGameRule(GameRule.DO_DAYLIGHT_CYCLE, false);
        w.setGameRule(GameRule.DO_MOB_SPAWNING, false);
        w.setGameRule(GameRule.DO_WEATHER_CYCLE, false);
        return w;
    }

    public World mineWorld() {
        return world;
    }

    private void keepMineLoaded() {
        int r = regionHalf / 16 + 1;
        int tickets = 0;
        for (int cx = -r; cx <= r; cx++) {
            for (int cz = -r; cz <= r; cz++) {
                world.getChunkAt(cx, cz).addPluginChunkTicket(this);
                tickets++;
            }
        }
        getLogger().info("Tickets de carga añadidos a " + tickets + " chunks de la mina");
    }

    public MineGenerator generator() {
        return generator;
    }

    public ExplorerMob explorer() {
        return explorer;
    }

    public String seed() {
        return seed;
    }

    @Override
    public boolean onCommand(@NotNull CommandSender sender, @NotNull Command command,
                             @NotNull String label, @NotNull String[] args) {
        if (!command.getName().equalsIgnoreCase("mineexp")) {
            return false;
        }
        if (args.length == 0) {
            sender.sendMessage("Uso: /mineexp <spawn|pos|regen|world>");
            return true;
        }
        switch (args[0].toLowerCase()) {
            case "spawn" -> {
                explorer.spawn();
                sender.sendMessage("Mob explorador re-desplegado.");
            }
            case "pos" -> {
                Location l = explorer.getLocation();
                sender.sendMessage(String.format("Mob en (%.1f, %.1f, %.1f)", l.getX(), l.getY(), l.getZ()));
            }
            case "regen" -> {
                if (args.length > 1) {
                    getConfig().set("seed", args[1]);
                    saveConfig();
                    seed = args[1];
                }
                generator.generateIfNeeded(true);
                explorer.spawn();
                sender.sendMessage("Mina regenerada con seed=" + seed);
            }
            case "world" -> {
                Location l = new Location(world, 0.5, startY + 4, 0.5);
                if (sender instanceof Player p) {
                    p.teleport(l);
                } else {
                    sender.sendMessage("Mundo mine: " + world.getName() + " spawn " + l);
                }
            }
            default -> sender.sendMessage("Subcomando desconocido.");
        }
        return true;
    }

    public List<String> onTabComplete(@NotNull CommandSender sender, @NotNull Command command,
                                      @NotNull String alias, @NotNull String[] args) {
        if (args.length == 1) {
            return List.of("spawn", "pos", "regen", "world");
        }
        return List.of();
    }

    /** Vigila la entidad exploradora para registrar la causa de su desaparición. */
    static final class EntityWatchdog implements Listener {
        private final ExplorerMob explorer;

        EntityWatchdog(ExplorerMob explorer) {
            this.explorer = explorer;
        }

        @EventHandler
        public void onDeath(EntityDeathEvent e) {
            if (explorer.tracks(e.getEntity())) {
                explorer.getPlugin().getLogger().warning("Mob explorador murió: " + e.getEntity().getType());
            }
        }

        @EventHandler
        public void onRemove(org.bukkit.event.entity.EntityRemoveEvent e) {
            if (explorer.tracks(e.getEntity())) {
                explorer.getPlugin().getLogger().warning(
                        "Mob explorador removido. Causa: " + e.getCause());
            } else {
                explorer.getPlugin().getLogger().info(
                        "Removida entidad " + e.getEntity().getType() + " causa=" + e.getCause());
            }
        }
    }
}
