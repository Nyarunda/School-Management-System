import { ReactNode } from "react";
import { Box, Group, Text, Title } from "@mantine/core";

export function WorkspaceHeader({ eyebrow, title, description, action }: {
  eyebrow?: string; title: string; description?: string; action?: ReactNode;
}) {
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" mb="lg">
      <Box style={{ minWidth: 0 }}>
        {eyebrow && <Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={4} style={{ letterSpacing: "0.04em" }}>{eyebrow}</Text>}
        <Title order={1} fz={26} fw={700}>{title}</Title>
        {description && <Text fz={13} c="dimmed" mt={4}>{description}</Text>}
      </Box>
      {action && <Group gap="xs" style={{ flexShrink: 0 }}>{action}</Group>}
    </Group>
  );
}
