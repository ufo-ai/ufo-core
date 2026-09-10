import { IconX } from "@tabler/icons-react";

import {
  Card,
  CardAction,
  CardButton,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/blocks/card";

export default function CardRtl() {
  return (
    <div dir="rtl">
      <Card>
        <CardHeader>
          <CardTitle>التخزين</CardTitle>
          <CardDescription>مساحة العمل عند 92 بالمئة من حصتها.</CardDescription>
          <CardAction>
            <button type="button" className="blk-card-close" aria-label="تجاهل">
              <IconX size={16} stroke={1.5} />
            </button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <p>يمكن أرشفة الملفات الأقدم من سنة لتحرير 18 غيغابايت.</p>
        </CardContent>
        <CardFooter>
          <CardButton variant="primary">أرشفة</CardButton>
          <CardButton variant="secondary">تجاهل</CardButton>
        </CardFooter>
      </Card>
    </div>
  );
}
