import { Fragment, useMemo, useState } from "react";
import {
  ActionIcon, Avatar, Badge, Box, Burger, Group, Menu, NavLink, ScrollArea, Select, Text,
  Tooltip, UnstyledButton, AppShell as MantineAppShell, useMantineTheme,
} from "@mantine/core";
import {
  IconArrowLeft, IconBell, IconLayoutDashboard, IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand,
  IconLogout, IconReportAnalytics, IconShieldCheck, IconStack2, IconVolume, IconVolumeOff,
} from "@tabler/icons-react";
import { useAuth, useAccess } from "../app/auth";
import { useDensity } from "../app/density";
import { navigation, NavItem } from "../app/navigation";
import { isSoundEnabled, setSoundEnabled } from "./notifications/notificationSound";
import { go, usePath } from "./ui";

function NavButton({ item, path, collapsed, onNavigate }: { item: NavItem; path: string; collapsed: boolean; onNavigate: () => void }) {
  const theme = useMantineTheme();
  if (!item.path) return null;
  const active = path === item.path || (item.path !== "/" && path.startsWith(item.path + "/"));
  const ItemIcon = item.icon;
  const link = (
    <NavLink
      component="button"
      type="button"
      active={active}
      variant="subtle"
      label={collapsed ? undefined : item.label}
      leftSection={<ItemIcon size={18} stroke={1.75} />}
      onClick={() => { go(item.path!); onNavigate(); }}
      c={active ? theme.other.sidebarActiveForeground : theme.other.sidebarForeground}
      bg={active ? theme.other.sidebarActive : undefined}
      style={{ borderLeft: `3px solid ${active ? theme.colors.indigo[6] : "transparent"}`, borderRadius: 6 }}
    />
  );
  return collapsed ? <Tooltip label={item.label} position="right" key={item.path}>{link}</Tooltip> : <Box key={item.path}>{link}</Box>;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { session, logout, selectTenant, platformAccess } = useAuth();
  const { canAccess } = useAccess();
  const theme = useMantineTheme();
  const path = usePath();
  const { density, toggleDensity } = useDensity();
  const [mobileOpened, setMobileOpened] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [soundOn, setSoundOn] = useState(() => isSoundEnabled());

  const groups = useMemo(
    () => navigation
      .map(item => item.children ? { ...item, children: item.children.filter(child => canAccess({ module: child.module, anyPermissions: child.permissions })) } : item)
      .filter(item => canAccess({ module: item.module, anyPermissions: item.permissions }) && (!item.children || item.children.length)),
    [session],
  );
  const current = groups.flatMap(item => item.children ?? [item]).find(item => item.path === path);
  const activeRole = session?.memberships?.find(m => m.tenant.slug === session.active_tenant?.slug)?.role;
  const initials = session?.user?.name?.slice(0, 2).toUpperCase();

  function toggleSound() { const next = !soundOn; setSoundEnabled(next); setSoundOn(next); }

  return (
    <MantineAppShell
      header={{ height: 64 }}
      navbar={{ width: collapsed ? 76 : 260, breakpoint: "sm", collapsed: { mobile: !mobileOpened } }}
      padding="md"
    >
      <MantineAppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={mobileOpened} onClick={() => setMobileOpened(o => !o)} hiddenFrom="sm" size="sm" />
            <ActionIcon variant="subtle" color="gray" visibleFrom="sm" aria-label={collapsed ? "Expand navigation" : "Collapse navigation"} onClick={() => setCollapsed(!collapsed)}>
              {collapsed ? <IconLayoutSidebarLeftExpand size={18} /> : <IconLayoutSidebarLeftCollapse size={18} />}
            </ActionIcon>
            <Text fw={600} size="sm" c={theme.other.textPrimary}>{current?.label ?? "Dashboard"}</Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <ActionIcon variant="subtle" color="gray" aria-label="Notifications" onClick={() => go("/communications/inbox")}><IconBell size={18} /></ActionIcon>
            <Select
              aria-label="Active school"
              data={(session?.memberships ?? []).map(item => ({ value: item.tenant.slug, label: item.tenant.name }))}
              value={session?.active_tenant?.slug ?? null}
              onChange={value => { if (value) void selectTenant(value); }}
              w={180}
              allowDeselect={false}
            />
            <Menu position="bottom-end" width={200}>
              <Menu.Target>
                <UnstyledButton>
                  <Group gap={8} wrap="nowrap">
                    <Avatar radius="xl" size={34} color="indigo">{initials}</Avatar>
                    <Box visibleFrom="sm" style={{ textAlign: "left" }}>
                      <Text size="sm" fw={600} lineClamp={1}>{session?.user?.name}</Text>
                      <Text size="xs" c="dimmed" lineClamp={1}>{activeRole}</Text>
                    </Box>
                  </Group>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconLogout size={14} />} onClick={() => void logout()}>Sign out</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Navbar className="app-shell-navbar" style={{ background: theme.other.sidebarBg, borderRight: `1px solid ${theme.other.sidebarBorder}` }}>
        <MantineAppShell.Section p="md">
          <Group gap={11} wrap="nowrap">
            <Box style={{ width: 34, height: 34, borderRadius: 9, background: "linear-gradient(145deg,#7180ff,#4254d8)", display: "grid", placeItems: "center", color: "#fff", fontWeight: 800, flexShrink: 0 }}>S</Box>
            {!collapsed && <Box><Text fw={700} size="sm" c={theme.other.sidebarForeground}>Scholaris</Text><Text size="xs" c={theme.other.sidebarMuted}>School ERP</Text></Box>}
          </Group>
        </MantineAppShell.Section>
        <MantineAppShell.Section grow component={ScrollArea} scrollbarSize={6} px="sm">
          {groups.map(item => item.children ? (
            <Box key={item.label} mb="md">
              {!collapsed && <Text size="xs" fw={700} tt="uppercase" c={theme.other.sidebarMuted} px={8} pb={6} style={{ letterSpacing: "0.08em" }}>{item.label}</Text>}
              <Box pl={collapsed ? 0 : 10} style={collapsed ? undefined : { borderLeft: `1px solid ${theme.other.sidebarBorder}` }}>
                {item.children.map((child, index) => {
                  const previousSection = index > 0 ? item.children![index - 1].section : undefined;
                  const showSection = !collapsed && Boolean(child.section) && child.section !== previousSection;
                  return (
                    <Fragment key={child.path}>
                      {showSection && (
                        <Text size="xs" fw={600} tt="uppercase" c={theme.other.sidebarMuted} pl={8} pt={index === 0 ? 0 : "xs"} pb={2} style={{ letterSpacing: "0.06em" }}>
                          {child.section}
                        </Text>
                      )}
                      <NavButton item={child} path={path} collapsed={collapsed} onNavigate={() => setMobileOpened(false)} />
                    </Fragment>
                  );
                })}
              </Box>
            </Box>
          ) : <NavButton key={item.path} item={item} path={path} collapsed={collapsed} onNavigate={() => setMobileOpened(false)} />)}
        </MantineAppShell.Section>
        <MantineAppShell.Section p="sm" style={{ borderTop: `1px solid ${theme.other.sidebarBorder}` }}>
          {platformAccess && (
            <NavLink component="button" type="button" variant="subtle" label={collapsed ? undefined : "Platform console"} leftSection={<IconShieldCheck size={18} />} onClick={() => go("/platform")} c="violet.7" />
          )}
          <NavLink component="button" type="button" variant="subtle" label={collapsed ? undefined : (density === "compact" ? "Comfortable density" : "Compact density")} leftSection={<IconStack2 size={18} />} onClick={toggleDensity} c={theme.other.sidebarMuted} />
          <NavLink component="button" type="button" variant="subtle" label={collapsed ? undefined : (soundOn ? "Sound on" : "Sound off")} leftSection={soundOn ? <IconVolume size={18} /> : <IconVolumeOff size={18} />} onClick={toggleSound} c={theme.other.sidebarMuted} />
        </MantineAppShell.Section>
      </MantineAppShell.Navbar>

      <MantineAppShell.Main className={`density-${density}`}>{children}</MantineAppShell.Main>
    </MantineAppShell>
  );
}

export function PlatformShell({ children }: { children: React.ReactNode }) {
  const { session, logout } = useAuth();
  const path = usePath();
  const theme = useMantineTheme();
  const initials = session?.user?.name?.slice(0, 2).toUpperCase();
  const items: { path: string; label: string; icon: NavItem["icon"] }[] = [
    { path: "/platform", label: "Overview", icon: IconLayoutDashboard },
    { path: "/platform/plans", label: "Plans", icon: IconStack2 },
    { path: "/platform/audit", label: "Audit trail", icon: IconReportAnalytics },
  ];
  return (
    <MantineAppShell header={{ height: 64 }} navbar={{ width: 260, breakpoint: "sm" }} padding="md">
      <MantineAppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Badge color="violet" variant="light" size="sm" tt="uppercase">Platform scope</Badge>
          <Menu position="bottom-end" width={200}>
            <Menu.Target>
              <UnstyledButton>
                <Group gap={8} wrap="nowrap">
                  <Avatar radius="xl" size={34} color="violet">{initials}</Avatar>
                  <Text size="sm" fw={600} visibleFrom="sm">{session?.user?.name}</Text>
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item leftSection={<IconLogout size={14} />} onClick={() => void logout()}>Sign out</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </MantineAppShell.Header>
      <MantineAppShell.Navbar className="app-shell-navbar" style={{ background: theme.other.sidebarBg, borderRight: `1px solid ${theme.other.sidebarBorder}` }}>
        <MantineAppShell.Section p="md">
          <Group gap={11} wrap="nowrap">
            <Box style={{ width: 34, height: 34, borderRadius: 9, background: "linear-gradient(145deg,#9b8cff,#684ac7)", display: "grid", placeItems: "center", color: "#fff", fontWeight: 800, flexShrink: 0 }}>P</Box>
            <Box><Text fw={700} size="sm" c={theme.other.sidebarForeground}>Scholaris Platform</Text><Text size="xs" c={theme.other.sidebarMuted}>Super administration</Text></Box>
          </Group>
        </MantineAppShell.Section>
        <MantineAppShell.Section grow px="sm">
          {items.map(item => (
            <NavLink key={item.path} component="button" type="button" variant="subtle" active={path === item.path}
              label={item.label} leftSection={<item.icon size={18} stroke={1.75} />} onClick={() => go(item.path)}
              c={path === item.path ? "violet.7" : theme.other.sidebarForeground}
              bg={path === item.path ? theme.other.sidebarActive : undefined}
              style={{ borderLeft: `3px solid ${path === item.path ? "var(--mantine-color-violet-6)" : "transparent"}`, borderRadius: 6 }} />
          ))}
        </MantineAppShell.Section>
        <MantineAppShell.Section p="sm" style={{ borderTop: `1px solid ${theme.other.sidebarBorder}` }}>
          <NavLink component="button" type="button" variant="subtle" label="Return to school ERP" leftSection={<IconArrowLeft size={18} />} onClick={() => go("/")} c={theme.other.sidebarMuted} />
        </MantineAppShell.Section>
      </MantineAppShell.Navbar>
      <MantineAppShell.Main>{children}</MantineAppShell.Main>
    </MantineAppShell>
  );
}
