import { Tabs } from "@mantine/core";

export function RecordTabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (value: string) => void }) {
  return (
    <Tabs value={value} onChange={v => v && onChange(v)} mb="md">
      <Tabs.List style={{ flexWrap: "nowrap", overflowX: "auto" }}>
        {tabs.map(name => <Tabs.Tab key={name} value={name} style={{ flexShrink: 0 }}>{name}</Tabs.Tab>)}
      </Tabs.List>
    </Tabs>
  );
}
