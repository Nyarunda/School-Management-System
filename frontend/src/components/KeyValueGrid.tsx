import { ReactNode } from "react";
import { Box, Divider, SimpleGrid, Text } from "@mantine/core";

export function KeyValueSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Box mb="lg">
      <Text fz={13} fw={600} tt="uppercase" c="dimmed" mb={6} style={{ letterSpacing: "0.05em" }}>{title}</Text>
      <Divider mb="sm" />
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
      <Text fz={14} fw={500}>{value}</Text>
    </Box>
  );
}
