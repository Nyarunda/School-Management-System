import { ReactNode } from "react";
import { Drawer, Stack, Text } from "@mantine/core";

export function DetailDrawer({ open, title, description, children, onClose }: {
  open: boolean; title: string; description?: string; children?: ReactNode; onClose: () => void;
}) {
  return <Drawer opened={open} onClose={onClose} title={title} position="right" size="md">
    <Stack gap="md">
      {description && <Text size="sm" c="dimmed">{description}</Text>}
      {children}
    </Stack>
  </Drawer>;
}
