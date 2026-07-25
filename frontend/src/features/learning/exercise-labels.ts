export function questionTypeLabel(type: string): string {
  switch (type) {
    case "single_choice":
      return "单选";
    case "multiple_choice":
      return "多选";
    case "true_false":
      return "判断";
    case "fill_blank":
      return "填空";
    case "short_answer":
      return "简答";
    case "mixed":
      return "混合";
    default:
      return "其他";
  }
}
