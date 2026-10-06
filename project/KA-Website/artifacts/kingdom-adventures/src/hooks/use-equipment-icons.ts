import { useQuery } from "@tanstack/react-query";
import { apiUrl } from "@/lib/api";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";

type SharedEquipmentIcons = {
  equipIcons?: Record<string, string>;
};

export function useEquipmentIcons() {
  const { data } = useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<SharedEquipmentIcons>(apiUrl("/shared")),
    staleTime: 15000,
  });
  return data?.equipIcons ?? {};
}
