import { ReactNode } from "react";
import { Box, Divider, Group, SimpleGrid, Text } from "@mantine/core";

export function KeyValueSection({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <Box mb="lg">
      <Group justify="space-between" align="center" mb={6}>
        <Text fz={13} fw={600} tt="uppercase" c="dimmed" style={{ letterSpacing: "0.05em" }}>{title}</Text>
        {action}
      </Group>
      <Divider mb="sm" color="indigo.3" />
      {children}
    </Box>
  );
}

export function KeyValueGrid({ children }: { children: ReactNode }) {
  return <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="md" verticalSpacing="sm">{children}</SimpleGrid>;
}

export function KeyValueItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box>
      <Text fz={12} c="dimmed">{label}</Text>
      <Text component="div" fz={14} fw={500}>{value}</Text>
    </Box>
  );
}
